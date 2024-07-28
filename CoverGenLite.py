import os
import shutil
import urllib.request
import zipfile
import gdown
import gradio as gr

from src.scripts.voice_conversion import conversion
from src.scripts.audio_processing import processing
from src.modules.model_management import *
from src.modules.ui_updates import *

now_dir = os.getcwd()
rvc_models_dir = os.path.join(now_dir, 'rvc_models')


if __name__ == '__main__':
    voice_models = ignore_files(rvc_models_dir)

    with gr.Blocks(title='CoverGen Lite - Politrees (v0.4)', theme=gr.themes.Soft(primary_hue="green", secondary_hue="green", neutral_hue="neutral", spacing_size="sm", radius_size="lg")) as app:
        with gr.Tab("Велком/Контакты"):
            gr.HTML("<center><h1>Добро пожаловать в CoverGen Lite - Politrees (v0.4)</h1></center>")
            with gr.Row():
                with gr.Column(variant='panel'):
                    gr.HTML("<center><h2><a href='https://t.me/Politrees2'>Telegram ЛС</a></h2></center>")
                    gr.HTML("<center><h2><a href='https://vk.com/artem__bebroy'>ВКонтакте (страница)</a></h2></center>")
                with gr.Column(variant='panel'):
                    gr.HTML("<center><h2><a href='https://t.me/pol1trees'>Telegram Канал</a></h2></center>")
                    gr.HTML("<center><h2><a href='https://t.me/+GMTP7hZqY0E4OGRi'>Telegram Чат</a></h2></center>")
            with gr.Column(variant='panel'):
                gr.HTML("<center><h2><a href='https://www.youtube.com/channel/UCHb3fZEVxUisnqLqCrEM8ZA'>YouTube</a></h2></center>")
                gr.HTML("<center><h2><a href='https://github.com/Bebra777228/Pol-Litres-RVC'>GitHub</a></h2></center>")

        with gr.Tab("Преобразование голоса"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=1, variant='panel'):
                    with gr.Group():
                        rvc_model = gr.Dropdown(voice_models, label='Голосовые модели:')
                        ref_btn = gr.Button('Обновить список моделей', variant='primary')
                    with gr.Group():
                        pitch = gr.Slider(-24, 24, value=0, step=0.5, label='Регулировка тона', info='-24 - мужской голос || 24 - женский голос')

                with gr.Column(scale=2, variant='panel'):
                    with gr.Column() as upload_file:
                        with gr.Group():
                            local_file = gr.Audio(label='Аудио', interactive=False, show_download_button=False, show_share_button=False)
                            uploaded_file = gr.UploadButton(label='Загрузить аудио-файл', file_types=['audio'], variant='primary')

                    with gr.Column(visible=False) as enter_local_file:
                        song_input = gr.Text(label='Путь к локальному файлу:', info='Введите полный путь к локальному файлу.')

                    with gr.Column():
                        show_upload_button = gr.Button('Загрузка файла с устройства', visible=False)
                        show_enter_button = gr.Button('Ввод пути к локальному файлу')
                    
                uploaded_file.upload(process_file_upload, inputs=[uploaded_file], outputs=[song_input, local_file])
                uploaded_file.upload(update_button_text, outputs=[uploaded_file])
                show_upload_button.click(swap_visibility, outputs=[upload_file, enter_local_file, song_input, local_file])
                show_enter_button.click(swap_visibility, outputs=[enter_local_file, upload_file, song_input, local_file])
                show_upload_button.click(swap_buttons, outputs=[show_upload_button, show_enter_button])
                show_enter_button.click(swap_buttons, outputs=[show_enter_button, show_upload_button])

            with gr.Group():
                with gr.Row(variant='panel'):
                    generate_btn = gr.Button("Генерировать", variant='primary', scale=1)
                    converted_voice = gr.Audio(label='Преобразованный голос', scale=5)
                    output_format = gr.Dropdown(['mp3', 'flac', 'wav'], value='mp3', label='Формат файла', scale=0.1, allow_custom_value=False, filterable=False)

            with gr.Accordion('Настройки преобразования голоса', open=False):
                with gr.Group():
                    with gr.Column(variant='panel'):
                        use_hybrid_methods = gr.Checkbox(label="Использовать гибридные методы", value=False)
                        f0_method = gr.Dropdown(['rmvpe+', 'fcpe', 'rmvpe', 'mangio-crepe', 'crepe'], value='rmvpe+', label='Метод выделения тона', allow_custom_value=False, filterable=False)
                        use_hybrid_methods.change(update_f0_method, inputs=use_hybrid_methods, outputs=f0_method)
                        crepe_hop_length = gr.Slider(8, 512, value=128, step=8, visible=False, label='Длина шага Crepe')
                        f0_method.change(show_hop_slider, inputs=f0_method, outputs=crepe_hop_length)
                        with gr.Row():
                            f0_min = gr.Slider(label="Минимальный диапазон тона", info="Определяет нижнюю границу диапазона тона, который алгоритм будет использовать для определения основной частоты (F0) в аудиосигнале.", step=1, minimum=1, value=50, maximum=100)
                            f0_max = gr.Slider(label="Максимальный диапазон тона", info="Определяет верхнюю границу диапазона тона, который алгоритм будет использовать для определения основной частоты (F0) в аудиосигнале.", step=1, minimum=400, value=1100, maximum=16000)
                    with gr.Column(variant='panel'):
                        index_rate = gr.Slider(0, 1, value=0, label='Влияние индекса', info='Контролирует степень влияния индексного файла на результат анализа. Более высокое значение увеличивает влияние индексного файла, но может усилить артефакты в аудио. Выбор более низкого значения может помочь снизить артефакты.')
                        filter_radius = gr.Slider(0, 7, value=3, step=1, label='Радиус фильтра', info='Управляет радиусом фильтрации результатов анализа тона. Если значение фильтрации равняется или превышает три, применяется медианная фильтрация для уменьшения шума дыхания в аудиозаписи.')
                        rms_mix_rate = gr.Slider(0, 1, value=0.25, step=0.01, label='Скорость смешивания RMS', info='Контролирует степень смешивания выходного сигнала с его оболочкой громкости. Значение близкое к 1 увеличивает использование оболочки громкости выходного сигнала, что может улучшить качество звука.')
                        protect = gr.Slider(0, 0.5, value=0.33, step=0.01, label='Защита согласных', info='Контролирует степень защиты отдельных согласных и звуков дыхания от электроакустических разрывов и других артефактов. Максимальное значение 0,5 обеспечивает наибольшую защиту, но может увеличить эффект индексирования, который может негативно влиять на качество звука. Уменьшение значения может уменьшить степень защиты, но снизить эффект индексирования.')

            ref_btn.click(update_models_list, None, outputs=rvc_model)
            generate_btn.click(conversion,
                              inputs=[uploaded_file, rvc_model, pitch, index_rate, filter_radius, rms_mix_rate, f0_method, crepe_hop_length, protect, output_format, f0_min, f0_max],
                              outputs=[converted_voice])

        with gr.Tab('Объединение/Обработка'):
            with gr.Row(equal_height=False):
                with gr.Column(variant='panel'):
                    with gr.Column() as upload_voc_file:
                        with gr.Group():
                            vocal_audio = gr.Audio(label='Вокал', interactive=False, show_download_button=False, show_share_button=False)
                            upload_vocal_audio = gr.UploadButton(label='Загрузить вокал', file_types=['audio'], variant='primary')

                    with gr.Column(visible=False) as enter_local_voc_file:
                        vocal_input = gr.Text(label='Путь к вокальному файлу', info='Введите полный путь к локальному вокальному файлу.')

                    with gr.Column():
                        show_upload_voc_button = gr.Button('Загрузка файла с устройства', visible=False)
                        show_enter_voc_button = gr.Button('Ввод пути к локальному файлу')
                
                upload_vocal_audio.upload(process_file_upload, inputs=[upload_vocal_audio], outputs=[vocal_input, vocal_audio])
                upload_vocal_audio.upload(update_button_text_voc, outputs=[upload_vocal_audio])
                show_upload_voc_button.click(swap_visibility, outputs=[upload_voc_file, enter_local_voc_file, vocal_input, vocal_audio])
                show_enter_voc_button.click(swap_visibility, outputs=[enter_local_voc_file, upload_voc_file, vocal_input, vocal_audio])
                show_upload_voc_button.click(swap_buttons, outputs=[show_upload_voc_button, show_enter_voc_button])
                show_enter_voc_button.click(swap_buttons, outputs=[show_enter_voc_button, show_upload_voc_button])

                with gr.Column(variant='panel'):
                    with gr.Column() as upload_inst_file:
                        with gr.Group():
                            instrumental_audio = gr.Audio(label='Инструментал', interactive=False, show_download_button=False, show_share_button=False)
                            upload_instrumental_audio = gr.UploadButton(label='Загрузить инструментал', file_types=['audio'], variant='primary')

                    with gr.Column(visible=False) as enter_local_inst_file:
                        instrumental_input = gr.Text(label='Путь к инструментальному файлу:', info='Введите полный путь к локальному инструментальному файлу.')

                    with gr.Column():
                        show_upload_inst_button = gr.Button('Загрузка файла с устройства', visible=False)
                        show_enter_inst_button = gr.Button('Ввод пути к локальному файлу')
                
                upload_instrumental_audio.upload(process_file_upload, inputs=[upload_instrumental_audio], outputs=[instrumental_input, instrumental_audio])
                upload_instrumental_audio.upload(update_button_text_inst, outputs=[upload_instrumental_audio])
                show_upload_inst_button.click(swap_visibility, outputs=[upload_inst_file, enter_local_inst_file, instrumental_input, instrumental_audio])
                show_enter_inst_button.click(swap_visibility, outputs=[enter_local_inst_file, upload_inst_file, instrumental_input, instrumental_audio])
                show_upload_inst_button.click(swap_buttons, outputs=[show_upload_inst_button, show_enter_inst_button])
                show_enter_inst_button.click(swap_buttons, outputs=[show_enter_inst_button, show_upload_inst_button])

            with gr.Group():
                with gr.Row(variant='panel'):
                    process_btn = gr.Button("Обработать", variant='primary', scale=1)
                    ai_cover = gr.Audio(label='Ai-Cover', scale=5)
                    output_format = gr.Dropdown(['mp3', 'flac', 'wav'], value='mp3', label='Формат файла', scale=0.1, allow_custom_value=False, filterable=False)

            with gr.Accordion('Настройки сведения аудио', open=False):
                gr.HTML('<center><h2>Изменение громкости</h2></center>')
                with gr.Row(variant='panel'):
                    vocal_gain = gr.Slider(-10, 10, value=0, step=1, label='Вокал', scale=1)
                    instrumental_gain = gr.Slider(-10, 10, value=0, step=1, label='Инструментал', scale=1)
                    clear_btn = gr.Button("Сбросить все эффекты", scale=0.1)

                use_effects = gr.Checkbox(label="Добавить эффекты на голос", value=False)
                with gr.Column(variant='panel', visible=False) as effects_accordion:
                    with gr.Accordion('Эффекты', open=True):
                        with gr.Accordion('Реверберация', open=False):
                            with gr.Group():
                                with gr.Column(variant='panel'):
                                    with gr.Row():
                                        reverb_rm_size = gr.Slider(0, 1, value=0.1, label='Размер комнаты', info='Этот параметр отвечает за размер виртуального помещения, в котором будет звучать реверберация. Большее значение означает больший размер комнаты и более длительное звучание реверберации.')
                                        reverb_width = gr.Slider(0, 1, value=1.0, label='Ширина реверберации', info='Этот параметр отвечает за ширину звучания реверберации. Чем выше значение, тем шире будет звучание реверберации.')
                                    with gr.Row():
                                        reverb_wet = gr.Slider(0, 1, value=0.1, label='Уровень влажности', info='Этот параметр отвечает за уровень реверберации. Чем выше значение, тем сильнее будет слышен эффект реверберации и тем дольше будет звучать «хвост».')
                                        reverb_dry = gr.Slider(0, 1, value=0.7, label='Уровень сухости', info='Этот параметр отвечает за уровень исходного звука без реверберации. Чем меньше значение, тем тише звук ai вокала. Если значение будет на 0, то исходный звук полностью исчезнет.')
                                    with gr.Row():
                                        reverb_damping = gr.Slider(0, 1, value=0.9, label='Уровень демпфирования', info='Этот параметр отвечает за поглощение высоких частот в реверберации. Чем выше его значение, тем сильнее будет поглощение частот и тем менее будет «яркий» звук реверберации.')

                        with gr.Accordion('Хорус', open=False):
                            with gr.Group():
                                with gr.Column(variant='panel'):
                                    with gr.Row():
                                        chorus_rate_hz = gr.Slider(0.1, 10, value=0, label='Скорость хоруса', info='Этот параметр отвечает за скорость колебаний эффекта хоруса в герцах. Чем выше значение, тем быстрее будут колебаться звуки.')
                                        chorus_depth = gr.Slider(0, 1, value=0, label='Глубина хоруса', info='Этот параметр отвечает за глубину эффекта хоруса. Чем выше значение, тем сильнее будет эффект хоруса.')
                                    with gr.Row():
                                        chorus_centre_delay_ms = gr.Slider(0, 50, value=0, label='Задержка центра (мс)', info='Этот параметр отвечает за задержку центрального сигнала эффекта хоруса в миллисекундах. Чем выше значение, тем дольше будет задержка.')
                                        chorus_feedback = gr.Slider(0, 1, value=0, label='Обратная связь', info='Этот параметр отвечает за уровень обратной связи эффекта хоруса. Чем выше значение, тем сильнее будет эффект обратной связи.')
                                    with gr.Row():
                                        chorus_mix = gr.Slider(0, 1, value=0, label='Смешение', info='Этот параметр отвечает за уровень смешивания оригинального сигнала и эффекта хоруса. Чем выше значение, тем сильнее будет эффект хоруса.')

                    with gr.Accordion('Обработка', open=True):
                        with gr.Accordion('Компрессор', open=False):
                            with gr.Row(variant='panel'):
                                compressor_ratio = gr.Slider(1, 20, value=4, label='Соотношение', info='Этот параметр контролирует количество применяемого сжатия аудио. Большее значение означает большее сжатие, которое уменьшает динамический диапазон аудио, делая громкие части более тихими и тихие части более громкими.')
                                compressor_threshold = gr.Slider(-60, 0, value=-12, label='Порог', info='Этот параметр устанавливает порог, при превышении которого начинает действовать компрессор. Компрессор сжимает громкие звуки, чтобы сделать звук более ровным. Чем ниже порог, тем большее количество звуков будет подвергнуто компрессии.')

                        with gr.Accordion('Фильтры', open=False):
                            with gr.Row(variant='panel'):
                                low_shelf_gain = gr.Slider(-20, 20, value=0, label='Фильтр нижних частот', info='Этот параметр контролирует усиление (громкость) низких частот. Положительное значение усиливает низкие частоты, делая звук более басским. Отрицательное значение ослабляет низкие частоты, делая звук более тонким.')
                                high_shelf_gain = gr.Slider(-20, 20, value=0, label='Фильтр высоких частот', info='Этот параметр контролирует усиление высоких частот. Положительное значение усиливает высокие частоты, делая звук более ярким. Отрицательное значение ослабляет высокие частоты, делая звук более тусклым.')

                        with gr.Accordion('Подавление шума', open=False):
                            with gr.Group():
                                with gr.Column(variant='panel'):
                                    with gr.Row():
                                        noise_gate_threshold = gr.Slider(-60, 0, value=-40, label='Порог', info='Этот параметр устанавливает пороговое значение в децибелах, ниже которого сигнал считается шумом. Когда сигнал опускается ниже этого порога, шумовой шлюз активируется и уменьшает громкость сигнала.')
                                        noise_gate_ratio = gr.Slider(1, 20, value=8, label='Соотношение', info='Этот параметр устанавливает уровень подавления шума. Большее значение означает более сильное подавление шума.')
                                    with gr.Row():
                                        noise_gate_attack = gr.Slider(0, 100, value=10, label='Время атаки (мс)', info='Этот параметр контролирует скорость, с которой шумовой шлюз открывается, когда звук становится достаточно громким. Большее значение означает, что шлюз открывается медленнее.')
                                        noise_gate_release = gr.Slider(0, 1000, value=100, label='Время спада (мс)', info='Этот параметр контролирует скорость, с которой шумовой шлюз закрывается, когда звук становится достаточно тихим. Большее значение означает, что шлюз закрывается медленнее.')

            use_effects.change(show_effects, inputs=use_effects, outputs=effects_accordion)
            process_btn.click(processing,
                            inputs=[upload_vocal_audio, upload_instrumental_audio, reverb_rm_size, reverb_wet, reverb_dry, reverb_damping,
                            reverb_width, low_shelf_gain, high_shelf_gain, compressor_ratio, compressor_threshold,
                            noise_gate_threshold, noise_gate_ratio, noise_gate_attack, noise_gate_release,
                            chorus_rate_hz, chorus_depth, chorus_centre_delay_ms, chorus_feedback, chorus_mix,
                            output_format, vocal_gain, instrumental_gain, use_effects],
                            outputs=[ai_cover])

            default_values = [0, 0, 0.1, 1.0, 0.1, 0.7, 0.9, 0, 0, 0, 0, 0, 4, -12, 0, 0, -40, 8, 10, 100]
            clear_btn.click(lambda: default_values,
                            outputs=[vocal_gain, instrumental_gain, reverb_rm_size, reverb_width, reverb_wet, reverb_dry, reverb_damping,
                            chorus_rate_hz, chorus_depth, chorus_centre_delay_ms, chorus_feedback, chorus_mix,
                            compressor_ratio, compressor_threshold, low_shelf_gain, high_shelf_gain, noise_gate_threshold,
                            noise_gate_ratio, noise_gate_attack, noise_gate_release])

        with gr.Tab('Загрузка модели'):
            with gr.Tab('Загрузить по ссылке'):
                with gr.Row():
                    with gr.Column(variant='panel'):
                        gr.HTML("<center><h3>Введите в поле ниже ссылку на ZIP-архив.</h3></center>")
                        model_zip_link = gr.Text(label='Ссылка на загрузку модели')
                    with gr.Column(variant='panel'):
                        with gr.Group():
                            model_name = gr.Text(label='Имя модели', info='Дайте вашей загружаемой модели уникальное имя, отличное от других голосовых моделей.')
                            download_btn = gr.Button('Загрузить модель', variant='primary')

                gr.HTML("<h3>Поддерживаемые сайты: <a href='https://huggingface.co/' target='_blank'>HuggingFace</a>, <a href='https://pixeldrain.com/' target='_blank'>Pixeldrain</a>, <a href='https://drive.google.com/' target='_blank'>Google Drive</a>, <a href='https://mega.nz/' target='_blank'>Mega</a>, <a href='https://disk.yandex.ru/' target='_blank'>Яндекс Диск</a></h3>")
                
                dl_output_message = gr.Text(label='Сообщение вывода', interactive=False)
                download_btn.click(download_from_url, inputs=[model_zip_link, model_name], outputs=dl_output_message)

            with gr.Tab('Загрузить ZIP архивом'):
                with gr.Row():
                    with gr.Column():
                        zip_file = gr.File(label='Zip-файл', file_types=['.zip'], file_count='single')
                    with gr.Column(variant='panel'):
                        gr.HTML("<h3>1. Найдите и скачайте файлы: .pth и необязательный файл .index</h3>")
                        gr.HTML("<h3>2. Закиньте файл(-ы) в ZIP-архив и поместите его в область загрузки</h3>")
                        gr.HTML('<h3>3. Дождитесь полной загрузки ZIP-архива в интерфейс</h3>')
                        with gr.Group():
                            local_model_name = gr.Text(label='Имя модели', info='Дайте вашей загружаемой модели уникальное имя, отличное от других голосовых моделей.')
                            model_upload_button = gr.Button('Загрузить модель', variant='primary')

                local_upload_output_message = gr.Text(label='Сообщение вывода', interactive=False)
                model_upload_button.click(upload_zip_model, inputs=[zip_file, local_model_name], outputs=local_upload_output_message)

            with gr.Tab('Загрузить файлами'):
                with gr.Group():
                    with gr.Row():
                        pth_file = gr.File(label='pth-файл', file_types=['.pth'], file_count='single')
                        index_file = gr.File(label='index-файл', file_types=['.index'], file_count='single')
                with gr.Column(variant='panel'):
                    with gr.Group():
                        separate_model_name = gr.Text(label='Имя модели', info='Дайте вашей загружаемой модели уникальное имя, отличное от других голосовых моделей.')
                        separate_upload_button = gr.Button('Загрузить модель', variant='primary')

                separate_upload_output_message = gr.Text(label='Сообщение вывода', interactive=False)
                separate_upload_button.click(upload_separate_files, inputs=[pth_file, index_file, separate_model_name], outputs=separate_upload_output_message)

    app.launch(share=True, show_error=True, quiet=True, show_api=False)