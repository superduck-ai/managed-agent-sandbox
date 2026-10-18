import argparse
import json
from pathlib import Path
import sys

from diagrams import render
from media import Edit, probe, transform
from providers import speech, transcribe


def main():
    parser = argparse.ArgumentParser(description='OMA local media and diagram tools. Outputs are written under /mnt/user-data/outputs.')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('probe')
    command.add_argument('input')
    command = commands.add_parser('transform', help='Pass a JSON Edit object, preferably using --file')
    group = command.add_mutually_exclusive_group(required=True)
    group.add_argument('--json')
    group.add_argument('--file')
    command = commands.add_parser('transcribe')
    command.add_argument('input')
    command.add_argument('output')
    command.add_argument('--engine', choices=['local', 'remote'], default='local')
    command.add_argument('--language')
    command.add_argument('--words', action='store_true')
    command.add_argument('--srt')
    command.add_argument('--text')
    command = commands.add_parser('speech')
    command.add_argument('text')
    command.add_argument('output')
    command.add_argument('--voice', default='en-us')
    command = commands.add_parser('render')
    command.add_argument('engine', choices=['graphviz', 'mermaid', 'd2', 'plantuml', 'typst'])
    command.add_argument('input')
    command.add_argument('output')
    args = parser.parse_args()
    if args.command == 'probe':
        result = probe(args.input)
    elif args.command == 'transform':
        result = transform(Edit.model_validate_json(Path(args.file).read_text() if args.file else args.json))
    elif args.command == 'transcribe':
        result = transcribe(args.input, args.output, args.engine, args.language, args.words, args.srt, args.text)
    elif args.command == 'speech':
        result = speech(args.text, args.output, voice=args.voice)
    else:
        result = render(args.engine, args.input, args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
