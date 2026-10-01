"""Local audio adapters; model weights cannot request executable Hub code."""
from pathlib import Path
import argparse
import json
import sys


def validate_waveform(waveform, sampling_rate):
    import numpy as np
    samples = np.asarray(waveform, dtype=np.float32).reshape(-1)
    if not 8000 <= sampling_rate <= 192000 or len(samples) < sampling_rate // 10:
        raise RuntimeError('Signal audio absent ou trop court.')
    if not np.isfinite(samples).all() or float(np.max(np.abs(samples))) < 1e-5:
        raise RuntimeError('Signal audio non fini ou silencieux ; aucune livraison.')
    if float(np.max(np.abs(samples))) > 1.0:
        raise RuntimeError('Signal audio saturé ; aucune livraison.')
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=['speak', 'transcribe'], required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--text')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import numpy as np
    import torch
    from transformers import AutoTokenizer, VitsModel, AutoProcessor, AutoModelForSpeechSeq2Seq, pipeline
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.candidate')
    if args.task == 'speak':
        if not args.text or not args.text.strip():
            raise ValueError('Texte à prononcer manquant.')
        from aurora_cli.evolution import load_policy
        if len(args.text) > int(load_policy().get('delivery_audio', {}).get('max_text_characters', 20000)):
            raise ValueError('Texte vocal hors du budget configuré ; aucune troncature.')
        tokenizer = AutoTokenizer.from_pretrained(str(args.model), local_files_only=True, trust_remote_code=False)
        model = VitsModel.from_pretrained(str(args.model), local_files_only=True, use_safetensors=True)
        # Keep every sentence; reject an unbounded job rather than truncate.
        sentences = __import__('re').split(r'(?<=[.!?])\s+', args.text.strip())
        chunks = []
        torch.manual_seed(0)
        for sentence in sentences:
            inputs = tokenizer(sentence, return_tensors='pt', truncation=False)
            if inputs['input_ids'].shape[-1] > 512:
                raise RuntimeError('Phrase trop longue pour ce moteur vocal ; aucune troncature.')
            with torch.inference_mode():
                samples = model(**inputs).waveform[0].cpu().numpy()
            chunks += [validate_waveform(samples, model.config.sampling_rate),
                       np.zeros(model.config.sampling_rate // 5, dtype=np.float32)]
        from scipy.io.wavfile import write
        write(str(temporary), model.config.sampling_rate, np.concatenate(chunks))
    else:
        if not args.input or not args.input.is_file():
            raise ValueError('Fichier audio à transcrire absent.')
        import soundfile as sf
        from aurora_cli.evolution import load_policy
        if sf.info(str(args.input)).duration > int(load_policy().get('delivery_audio', {}).get('max_input_duration_s', 3600)):
            raise ValueError('Audio hors du budget configuré ; découpe nécessaire, aucune troncature.')
        from scipy.signal import resample_poly
        from math import gcd
        samples, rate = sf.read(str(args.input), dtype='float32', always_2d=True)
        samples = samples.mean(axis=1)
        if not len(samples) or not np.isfinite(samples).all():
            raise ValueError('Fichier audio vide ou non fini.')
        processor = AutoProcessor.from_pretrained(str(args.model), local_files_only=True, trust_remote_code=False)
        model = AutoModelForSpeechSeq2Seq.from_pretrained(str(args.model), local_files_only=True, use_safetensors=True)
        divisor = gcd(rate, 16000)
        samples = resample_poly(samples, 16000 // divisor, rate // divisor)
        recognizer = pipeline('automatic-speech-recognition', model=model,
            tokenizer=processor.tokenizer, feature_extractor=processor.feature_extractor,
            device='cpu', chunk_length_s=30)
        result = recognizer({'array': samples, 'sampling_rate': 16000})
        if not result.get('text', '').strip():
            raise RuntimeError('Transcription vide ; aucune livraison.')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.output.exists():
        raise FileExistsError('Destination existante conservée ; choisis un autre nom.')
    temporary.replace(args.output)
    print(f'Audio produit et contrôlé structurellement : {args.output}', flush=True)


if __name__ == '__main__':
    main()
