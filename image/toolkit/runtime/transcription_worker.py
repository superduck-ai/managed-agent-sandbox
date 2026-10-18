import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--model', required=True)
    parser.add_argument('--language')
    parser.add_argument('--words', action='store_true')
    args = parser.parse_args()
    model = WhisperModel(args.model, device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
    segments, info = model.transcribe(args.input, language=args.language, word_timestamps=args.words, beam_size=3, vad_filter=True)
    rows = []
    for segment in segments:
        row = {'start': segment.start, 'end': segment.end, 'text': segment.text}
        if args.words:
            row['words'] = [{'start': w.start, 'end': w.end, 'word': w.word, 'probability': w.probability} for w in segment.words or []]
        rows.append(row)
    Path(args.output).write_text(json.dumps({'language': info.language, 'text': ''.join(r['text'] for r in rows).strip(), 'segments': rows}, ensure_ascii=False))


if __name__ == '__main__':
    main()
