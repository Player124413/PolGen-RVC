import math
import os
import torch
from torch import nn
from torch.nn import functional as F
from torch.nn import Conv1d, ConvTranspose1d, Conv2d
from torch.nn.utils import weight_norm, remove_weight_norm, spectral_norm
import numpy as np

now_dir = os.getcwd()

from src.infer_pack import modules
from src.infer_pack import attentions
from src.infer_pack.commons import init_weights, get_padding, sequence_mask, rand_slice_segments, slice_segments2


class TextEncoder(nn.Module):
    def __init__(self, input_dim, out_channels, hidden_channels, filter_channels, n_heads, n_layers, kernel_size, p_dropout, f0=True):
        super().__init__()
        self.out_channels = out_channels
        self.hidden_channels = hidden_channels
        self.emb_phone = nn.Linear(input_dim, hidden_channels)
        self.lrelu = nn.LeakyReLU(0.1, inplace=True)
        if f0:
            self.emb_pitch = nn.Embedding(256, hidden_channels)
        self.encoder = attentions.Encoder(hidden_channels, filter_channels, n_heads, n_layers, kernel_size, p_dropout)
        self.proj = nn.Conv1d(hidden_channels, out_channels * 2, 1)

    def forward(self, phone, pitch, lengths):
        x = self.emb_phone(phone) + (self.emb_pitch(pitch) if pitch is not None else 0)
        x = x * math.sqrt(self.hidden_channels)
        x = self.lrelu(x).transpose(1, -1)
        x_mask = torch.unsqueeze(sequence_mask(lengths, x.size(2)), 1).to(x.dtype)
        x = self.encoder(x * x_mask, x_mask)
        stats = self.proj(x) * x_mask
        return torch.split(stats, self.out_channels, dim=1), x_mask


class ResidualCouplingBlock(nn.Module):
    def __init__(self, channels, hidden_channels, kernel_size, dilation_rate, n_layers, n_flows=4, gin_channels=0):
        super().__init__()
        self.flows = nn.ModuleList([
            modules.ResidualCouplingLayer(channels, hidden_channels, kernel_size, dilation_rate, n_layers, gin_channels=gin_channels, mean_only=True),
            modules.Flip()
        ] * n_flows)

    def forward(self, x, x_mask, g=None, reverse=False):
        flows = reversed(self.flows) if reverse else self.flows
        for flow in flows:
            x = flow(x, x_mask, g=g, reverse=reverse)
        return x

    def remove_weight_norm(self):
        for flow in self.flows[::2]:
            flow.remove_weight_norm()


class PosteriorEncoder(nn.Module):
    def __init__(self, in_channels, out_channels, hidden_channels, kernel_size, dilation_rate, n_layers, gin_channels=0):
        super().__init__()
        self.pre = nn.Conv1d(in_channels, hidden_channels, 1)
        self.enc = modules.WN(hidden_channels, kernel_size, dilation_rate, n_layers, gin_channels=gin_channels)
        self.proj = nn.Conv1d(hidden_channels, out_channels * 2, 1)

    def forward(self, x, x_lengths, g=None):
        x_mask = torch.unsqueeze(sequence_mask(x_lengths, x.size(2)), 1).to(x.dtype)
        x = self.pre(x) * x_mask
        x = self.enc(x, x_mask, g=g)
        stats = self.proj(x) * x_mask
        m, logs = torch.split(stats, self.out_channels, dim=1)
        z = (m + torch.randn_like(m) * torch.exp(logs)) * x_mask
        return z, m, logs, x_mask

    def remove_weight_norm(self):
        self.enc.remove_weight_norm()


class Generator(nn.Module):
    def __init__(self, initial_channel, resblock, resblock_kernel_sizes, resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes, gin_channels=0):
        super().__init__()
        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_rates)
        self.conv_pre = Conv1d(initial_channel, upsample_initial_channel, 7, 1, padding=3)
        self.ups = nn.ModuleList([
            weight_norm(ConvTranspose1d(upsample_initial_channel // (2**i), upsample_initial_channel // (2**(i+1)), k, u, padding=(k - u) // 2))
            for i, (u, k) in enumerate(zip(upsample_rates, upsample_kernel_sizes))
        ])
        self.resblocks = nn.ModuleList([
            resblock(upsample_initial_channel // (2**(i+1)), k, d)
            for i in range(len(self.ups))
            for k, d in zip(resblock_kernel_sizes, resblock_dilation_sizes)
        ])
        self.conv_post = Conv1d(upsample_initial_channel // (2**len(upsample_rates)), 1, 7, 1, padding=3, bias=False)
        self.ups.apply(init_weights)
        if gin_channels != 0:
            self.cond = nn.Conv1d(gin_channels, upsample_initial_channel, 1)

    def forward(self, x, g=None):
        x = self.conv_pre(x)
        if g is not None:
            x += self.cond(g)
        for i in range(self.num_upsamples):
            x = F.leaky_relu(x, modules.LRELU_SLOPE)
            x = self.ups[i](x)
            x_source = None
            for j in range(self.num_kernels):
                x_source = self.resblocks[i * self.num_kernels + j](x) if x_source is None else x_source + self.resblocks[i * self.num_kernels + j](x)
            x = x_source / self.num_kernels
        x = F.leaky_relu(x)
        return torch.tanh(self.conv_post(x))

    def remove_weight_norm(self):
        for l in self.ups:
            remove_weight_norm(l)
        for l in self.resblocks:
            l.remove_weight_norm()


class SineGen(nn.Module):
    def __init__(self, samp_rate, harmonic_num=0, sine_amp=0.1, noise_std=0.003, voiced_threshold=0, flag_for_pulse=False):
        super().__init__()
        self.sine_amp = sine_amp
        self.noise_std = noise_std
        self.harmonic_num = harmonic_num
        self.dim = harmonic_num + 1
        self.sampling_rate = samp_rate
        self.voiced_threshold = voiced_threshold

    def _f02uv(self, f0):
        uv = torch.ones_like(f0) * (f0 > self.voiced_threshold)
        if uv.device.type == "privateuseone":
            uv = uv.float()
        return uv

    def forward(self, f0, upp):
        with torch.no_grad():
            f0 = f0[:, None].transpose(1, 2)
            f0_buf = torch.zeros(f0.shape[0], f0.shape[1], self.dim, device=f0.device)
            f0_buf[:, :, 0] = f0[:, :, 0]
            for idx in np.arange(self.harmonic_num):
                f0_buf[:, :, idx + 1] = f0_buf[:, :, 0] * (idx + 2)
            rad_values = (f0_buf / self.sampling_rate) % 1
            rand_ini = torch.rand(f0_buf.shape[0], f0_buf.shape[2], device=f0_buf.device)
            rand_ini[:, 0] = 0
            rad_values[:, 0, :] += rand_ini
            tmp_over_one = torch.cumsum(rad_values, 1) * upp
            tmp_over_one = F.interpolate(tmp_over_one.transpose(2, 1), scale_factor=upp, mode="linear", align_corners=True).transpose(2, 1)
            rad_values = F.interpolate(rad_values.transpose(2, 1), scale_factor=upp, mode="nearest").transpose(2, 1) % 1
            tmp_over_one %= 1
            tmp_over_one_idx = (tmp_over_one[:, 1:, :] - tmp_over_one[:, :-1, :]) < 0
            cumsum_shift = torch.zeros_like(rad_values)
            cumsum_shift[:, 1:, :] = tmp_over_one_idx * -1.0
            sine_waves = torch.sin(torch.cumsum(rad_values + cumsum_shift, dim=1) * 2 * np.pi) * self.sine_amp
            uv = self._f02uv(f0)
            uv = F.interpolate(uv.transpose(2, 1), scale_factor=upp, mode="nearest").transpose(2, 1)
            noise_amp = uv * self.noise_std + (1 - uv) * self.sine_amp / 3
            noise = noise_amp * torch.randn_like(sine_waves)
            sine_waves = sine_waves * uv + noise
        return sine_waves, uv, noise


class SourceModuleHnNSF(nn.Module):
    def __init__(self, sampling_rate, harmonic_num=0, sine_amp=0.1, add_noise_std=0.003, voiced_threshod=0, is_half=True):
        super().__init__()
        self.sine_amp = sine_amp
        self.noise_std = add_noise_std
        self.is_half = is_half
        self.l_sin_gen = SineGen(sampling_rate, harmonic_num, sine_amp, add_noise_std, voiced_threshod)
        self.l_linear = nn.Linear(harmonic_num + 1, 1)
        self.l_tanh = nn.Tanh()

    def forward(self, x, upp=None):
        if not hasattr(self, "ddtype"):
            self.ddtype = self.l_linear.weight.dtype
        sine_wavs, uv, _ = self.l_sin_gen(x, upp)
        if sine_wavs.dtype != self.ddtype:
            sine_wavs = sine_wavs.to(self.ddtype)
        sine_merge = self.l_tanh(self.l_linear(sine_wavs))
        return sine_merge, None, None


class GeneratorNSF(nn.Module):
    def __init__(self, initial_channel, resblock, resblock_kernel_sizes, resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes, gin_channels, sr, is_half=False):
        super().__init__()
        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_rates)
        self.f0_upsamp = nn.Upsample(scale_factor=np.prod(upsample_rates))
        self.m_source = SourceModuleHnNSF(sampling_rate=sr, harmonic_num=0, is_half=is_half)
        self.noise_convs = nn.ModuleList([
            Conv1d(1, upsample_initial_channel // (2**(i+1)), kernel_size=(stride_f0 * 2) if i + 1 < len(upsample_rates) else 1, stride=stride_f0, padding=stride_f0 // 2)
            for i, stride_f0 in enumerate([np.prod(upsample_rates[i + 1 :])] * (len(upsample_rates) - 1) + [1])
        ])
        self.conv_pre = Conv1d(initial_channel, upsample_initial_channel, 7, 1, padding=3)
        self.ups = nn.ModuleList([
            weight_norm(ConvTranspose1d(upsample_initial_channel // (2**i), upsample_initial_channel // (2**(i+1)), k, u, padding=(k - u) // 2))
            for i, (u, k) in enumerate(zip(upsample_rates, upsample_kernel_sizes))
        ])
        self.resblocks = nn.ModuleList([
            resblock(upsample_initial_channel // (2**(i+1)), k, d)
            for i in range(len(self.ups))
            for k, d in zip(resblock_kernel_sizes, resblock_dilation_sizes)
        ])
        self.conv_post = Conv1d(upsample_initial_channel // (2**len(upsample_rates)), 1, 7, 1, padding=3, bias=False)
        self.ups.apply(init_weights)
        if gin_channels != 0:
            self.cond = nn.Conv1d(gin_channels, upsample_initial_channel, 1)
        self.upp = np.prod(upsample_rates)

    def forward(self, x, f0, g=None):
        har_source, noi_source, uv = self.m_source(f0, self.upp)
        har_source = har_source.transpose(1, 2)
        x = self.conv_pre(x)
        if g is not None:
            x += self.cond(g)
        for i in range(self.num_upsamples):
            x = F.leaky_relu(x, modules.LRELU_SLOPE)
            x = self.ups[i](x)
            x += self.noise_convs[i](har_source)
            x_source = None
            for j in range(self.num_kernels):
                x_source = self.resblocks[i * self.num_kernels + j](x) if x_source is None else x_source + self.resblocks[i * self.num_kernels + j](x)
            x = x_source / self.num_kernels
        x = F.leaky_relu(x)
        return torch.tanh(self.conv_post(x))

    def remove_weight_norm(self):
        for l in self.ups:
            remove_weight_norm(l)
        for l in self.resblocks:
            l.remove_weight_norm()


sr2sr = {
    "32k": 32000,
    "40k": 40000,
    "48k": 48000
}


class SynthesizerTrnMsNSFsid(nn.Module):
    def __init__(self, input_dim, spec_channels, segment_size, inter_channels, hidden_channels, filter_channels, n_heads, n_layers, kernel_size, p_dropout, resblock, resblock_kernel_sizes, resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes, spk_embed_dim, gin_channels, sr, is_half=False, f0=True):
        super().__init__()
        if isinstance(sr, str):
            sr = sr2sr[sr]
        self.spec_channels = spec_channels
        self.segment_size = segment_size
        self.gin_channels = gin_channels
        self.spk_embed_dim = spk_embed_dim
        self.enc_p = TextEncoder(input_dim, inter_channels, hidden_channels, filter_channels, n_heads, n_layers, kernel_size, p_dropout, f0=f0)
        self.dec = GeneratorNSF(inter_channels, resblock, resblock_kernel_sizes, resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes, gin_channels=gin_channels, sr=sr, is_half=is_half)
        self.enc_q = PosteriorEncoder(spec_channels, inter_channels, hidden_channels, 5, 1, 16, gin_channels=gin_channels)
        self.flow = ResidualCouplingBlock(inter_channels, hidden_channels, 5, 1, 3, gin_channels=gin_channels)
        self.emb_g = nn.Embedding(spk_embed_dim, gin_channels)

    def remove_weight_norm(self):
        self.dec.remove_weight_norm()
        self.flow.remove_weight_norm()
        self.enc_q.remove_weight_norm()

    def forward(self, phone, phone_lengths, pitch, pitchf, y, y_lengths, ds):
        g = self.emb_g(ds).unsqueeze(-1)
        m_p, logs_p, x_mask = self.enc_p(phone, pitch, phone_lengths)
        z, m_q, logs_q, y_mask = self.enc_q(y, y_lengths, g=g)
        z_p = self.flow(z, y_mask, g=g)
        z_slice, ids_slice = rand_slice_segments(z, y_lengths, self.segment_size)
        pitchf = slice_segments2(pitchf, ids_slice, self.segment_size)
        o = self.dec(z_slice, pitchf, g=g)
        return o, ids_slice, x_mask, y_mask, (z, z_p, m_p, logs_p, m_q, logs_q)

    def infer(self, phone, phone_lengths, pitch, nsff0, sid, rate=None):
        g = self.emb_g(sid).unsqueeze(-1)
        m_p, logs_p, x_mask = self.enc_p(phone, pitch, phone_lengths)
        z_p = (m_p + torch.exp(logs_p) * torch.randn_like(m_p) * 0.66666) * x_mask
        if rate:
            head = int(z_p.shape[2] * rate)
            z_p, x_mask, nsff0 = z_p[:, :, -head:], x_mask[:, :, -head:], nsff0[:, -head:]
        z = self.flow(z_p, x_mask, g=g, reverse=True)
        return self.dec(z * x_mask, nsff0, g=g), x_mask, (z, z_p, m_p, logs_p)


class SynthesizerTrnMsNSFsid_nono(SynthesizerTrnMsNSFsid):
    def __init__(self, input_dim, spec_channels, segment_size, inter_channels, hidden_channels, filter_channels, n_heads, n_layers, kernel_size, p_dropout, resblock, resblock_kernel_sizes, resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes, spk_embed_dim, gin_channels, sr=None):
        super().__init__(input_dim, spec_channels, segment_size, inter_channels, hidden_channels, filter_channels, n_heads, n_layers, kernel_size, p_dropout, resblock, resblock_kernel_sizes, resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes, spk_embed_dim, gin_channels, sr, f0=False)


class MultiPeriodDiscriminator(nn.Module):
    def __init__(self, use_spectral_norm=False, periods=[2, 3, 5, 7, 11, 17]):
        super().__init__()
        self.discriminators = nn.ModuleList([DiscriminatorS(use_spectral_norm=use_spectral_norm)] + [DiscriminatorP(p, use_spectral_norm=use_spectral_norm) for p in periods])

    def forward(self, y, y_hat):
        results = [d(y) for d in self.discriminators]
        results_hat = [d(y_hat) for d in self.discriminators]
        y_d_rs, fmap_rs = zip(*results)
        y_d_gs, fmap_gs = zip(*results_hat)
        return y_d_rs, y_d_gs, fmap_rs, fmap_gs


class MultiPeriodDiscriminatorV2(MultiPeriodDiscriminator):
    def __init__(self, use_spectral_norm=False, periods=[2, 3, 5, 7, 11, 17, 23, 37]):
        super().__init__(use_spectral_norm, periods)


class DiscriminatorS(nn.Module):
    def __init__(self, use_spectral_norm=False):
        super().__init__()
        norm_f = spectral_norm if use_spectral_norm else weight_norm
        self.convs = nn.ModuleList([
            norm_f(Conv1d(1, 16, 15, 1, padding=7)),
            norm_f(Conv1d(16, 64, 41, 4, groups=4, padding=20)),
            norm_f(Conv1d(64, 256, 41, 4, groups=16, padding=20)),
            norm_f(Conv1d(256, 1024, 41, 4, groups=64, padding=20)),
            norm_f(Conv1d(1024, 1024, 41, 4, groups=256, padding=20)),
            norm_f(Conv1d(1024, 1024, 5, 1, padding=2))
        ])
        self.conv_post = norm_f(Conv1d(1024, 1, 3, 1, padding=1))

    def forward(self, x):
        fmap = [F.leaky_relu(conv(x), modules.LRELU_SLOPE) for conv in self.convs]
        x = torch.flatten(self.conv_post(fmap[-1]), 1, -1)
        return x, fmap


class DiscriminatorP(nn.Module):
    def __init__(self, period, kernel_size=5, stride=3, use_spectral_norm=False):
        super().__init__()
        self.period = period
        norm_f = spectral_norm if use_spectral_norm else weight_norm
        self.convs = nn.ModuleList([
            norm_f(Conv2d(1, 32, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
            norm_f(Conv2d(32, 128, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
            norm_f(Conv2d(128, 512, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
            norm_f(Conv2d(512, 1024, (kernel_size, 1), (stride, 1), padding=(get_padding(kernel_size, 1), 0))),
            norm_f(Conv2d(1024, 1024, (kernel_size, 1), 1, padding=(get_padding(kernel_size, 1), 0)))
        ])
        self.conv_post = norm_f(Conv2d(1024, 1, (3, 1), 1, padding=(1, 0)))

    def forward(self, x):
        b, c, t = x.shape
        if t % self.period != 0:
            n_pad = self.period - (t % self.period)
            x = F.pad(x, (0, n_pad), "reflect")
            t += n_pad
        x = x.view(b, c, t // self.period, self.period)
        fmap = [F.leaky_relu(conv(x), modules.LRELU_SLOPE) for conv in self.convs]
        x = torch.flatten(self.conv_post(fmap[-1]), 1, -1)
        return x, fmap
