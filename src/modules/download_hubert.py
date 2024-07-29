import os
import shutil
import urllib.request

now_dir = os.getcwd()
rvc_models_dir = os.path.join(now_dir, 'rvc_models')
hubert_base_path = os.path.join(rvc_models_dir, 'hubert_base.pt')

base_url = 'https://huggingface.co/Politrees/all_RVC-pretrained_and_other/resolve/main/HuBERTs/'

models = {
    'Стандарт': 'hubert_base.pt',
    'Улучшенный стандарт': 'contentvec_base.pt',
    'Корейский базовый': 'korean_hubert_base.pt',
    'Китайский базовый': 'chinese_hubert_base.pt',
    'Китайский большой': 'chinese_hubert_large.pt',
    'Японский базовый': 'japanese_hubert_base.pt',
    'Японский большой': 'japanese_hubert_large.pt'
}

def download_and_replace_model(model_desc):
    model_name = models[model_desc]
    model_url = base_url + model_name
    tmp_model_path = os.path.join(rvc_models_dir, 'tmp_model.pt')
    
    with urllib.request.urlopen(model_url) as response, open(tmp_model_path, 'wb') as out_file:
        shutil.copyfileobj(response, out_file)
    
    if os.path.exists(hubert_base_path):
        os.remove(hubert_base_path)
    
    os.rename(tmp_model_path, hubert_base_path)
    
    return f"Модель {model_desc} успешно установлена."
