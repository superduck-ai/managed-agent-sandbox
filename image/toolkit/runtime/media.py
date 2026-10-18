import json
from pathlib import Path
import tempfile
from typing import Literal
import zipfile

from pydantic import BaseModel, ConfigDict, Field

from common import input_file, output_file, publish, run, staging


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    action: Literal['clip', 'transcode', 'audio', 'frames', 'gif', 'subtitle', 'concat', 'mix']
    input_path: str
    output_path: str
    start: float = Field(default=0, ge=0, le=86400)
    duration: float | None = Field(default=None, gt=0, le=1800)
    width: int | None = Field(default=None, ge=64, le=3840, multiple_of=2)
    crf: int = Field(default=23, ge=0, le=51)
    fps: float = Field(default=1, gt=0, le=30)
    max_frames: int = Field(default=12, ge=1, le=120)
    subtitle_path: str | None = None
    additional_paths: list[str] = Field(default_factory=list, max_length=20)


def probe(input_path):
    source = input_file(input_path)
    return probe_source(source)


def probe_source(source):
    data = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,sample_rate,channels,r_frame_rate', '-of', 'json', source], timeout=30))
    return {'file_path': str(source), **data}


def ffmpeg(inputs):
    arguments = ['ffmpeg', '-hide_banner', '-v', 'error', '-nostdin', '-n']
    for source in inputs:
        arguments.extend(['-protocol_whitelist', 'file,pipe', '-i', str(source)])
    return arguments


def transform(edit):
    source = input_file(edit.input_path)
    extension = { 'frames': {'.zip'}, 'gif': {'.gif'}, 'audio': {'.wav', '.mp3', '.m4a', '.flac'} }.get(edit.action, {'.mp4', '.mov', '.mkv', '.webm'} if edit.action != 'mix' else {'.wav', '.mp3', '.m4a', '.flac'})
    destination = output_file(edit.output_path, extension)
    details = probe(str(source))
    seconds = float(details.get('format', {}).get('duration', 0))
    if seconds <= 0 or seconds > 1800 or edit.start >= seconds:
        raise ValueError('Input duration must be between 0 and 1800 seconds; start must precede the end')
    if edit.action == 'frames':
        return frames(source, destination, edit)
    additional = [input_file(p) for p in edit.additional_paths]
    if edit.action not in {'concat', 'mix'} and additional:
        raise ValueError('additional_paths requires concat or mix')
    with tempfile.TemporaryDirectory(prefix='oma-media-') as workspace, staging(destination) as temporary:
        work = Path(workspace)
        filters = []
        arguments = ffmpeg([source, *additional])
        if edit.action in {'concat', 'mix'}:
            if not additional:
                raise ValueError('concat and mix require additional_paths')
            if edit.action == 'mix':
                filters = [f'amix=inputs={len(additional) + 1}:duration=longest:normalize=1']
                arguments.extend(['-filter_complex', filters[0], '-vn'])
            else:
                sources = [details, *[probe(str(path)) for path in additional]]
                if sum(float(p.get('format', {}).get('duration', 0)) for p in sources) > 1800:
                    raise ValueError('Concatenated output exceeds 1800 seconds')
                for other in sources:
                    if not any(s.get('codec_type') == 'video' for s in other.get('streams', [])):
                        raise ValueError('concat requires video inputs')
                audio = [any(s.get('codec_type') == 'audio' for s in p.get('streams', [])) for p in sources]
                if any(audio) and not all(audio):
                    raise ValueError('concat inputs must all include audio or all omit it; normalize the inputs first')
                chains = ''.join(f'[{i}:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,setpts=PTS-STARTPTS[v{i}];' for i in range(len(additional) + 1))
                if all(audio):
                    chains += ''.join(f'[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}];' for i in range(len(additional) + 1))
                    chains += ''.join(f'[v{i}][a{i}]' for i in range(len(additional) + 1)) + f'concat=n={len(additional) + 1}:v=1:a=1[v][a]'
                    arguments.extend(['-filter_complex', chains, '-map', '[v]', '-map', '[a]'])
                else:
                    chains += ''.join(f'[v{i}]' for i in range(len(additional) + 1)) + f'concat=n={len(additional) + 1}:v=1:a=0[v]'
                    arguments.extend(['-filter_complex', chains, '-map', '[v]', '-an'])
        else:
            arguments.extend(['-ss', str(edit.start)])
            if edit.duration is not None:
                arguments.extend(['-t', str(edit.duration)])
            if edit.width:
                filters.append(f'scale={edit.width}:-2')
            if edit.action == 'audio':
                arguments.extend(['-vn', '-ac', '1', '-ar', '16000'])
            elif edit.action == 'gif':
                filters.append(f'fps={min(edit.fps, 15)}')
                arguments.extend(['-an', '-t', str(min(edit.duration or 10, 30))])
            elif edit.action == 'subtitle':
                if not edit.subtitle_path:
                    raise ValueError('subtitle requires subtitle_path')
                subtitle = input_file(edit.subtitle_path, 1 << 20)
                if subtitle.suffix.lower() not in {'.srt', '.ass', '.vtt'}:
                    raise ValueError('Subtitles must be SRT, ASS or VTT')
                local = work / ('captions' + subtitle.suffix.lower())
                local.write_bytes(subtitle.read_bytes())
                filters.append(f"subtitles=filename='{local}':force_style='FontName=Noto Sans CJK SC,FontSize=20'")
            elif edit.subtitle_path:
                raise ValueError('subtitle_path requires subtitle action')
            if filters:
                arguments.extend(['-vf', ','.join(filters)])
        if destination.suffix.lower() in {'.mp4', '.mov', '.mkv'}:
            arguments.extend(['-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(edit.crf), '-pix_fmt', 'yuv420p', '-c:a', 'aac'])
            if destination.suffix.lower() in {'.mp4', '.mov'}:
                arguments.extend(['-movflags', '+faststart'])
        elif destination.suffix.lower() == '.webm':
            arguments.extend(['-c:v', 'libvpx-vp9', '-crf', str(edit.crf), '-b:v', '0', '-c:a', 'libopus'])
        run([*arguments, temporary])
        result = probe_source(temporary)
        result['file_path'] = str(destination)
        return {**publish(temporary, destination), 'action': edit.action, 'media': result}


def frames(source, destination, edit):
    with tempfile.TemporaryDirectory(prefix='oma-frames-') as directory, staging(destination) as temporary:
        work = Path(directory)
        pattern = work / 'frame-%03d.png'
        arguments = [*ffmpeg([source]), '-ss', str(edit.start)]
        if edit.duration is not None:
            arguments.extend(['-t', str(edit.duration)])
        arguments.extend(['-vf', f'fps={edit.fps},scale={edit.width or 640}:-2', '-frames:v', str(edit.max_frames), pattern])
        run(arguments)
        images = sorted(work.glob('frame-*.png'))
        if not images:
            raise ValueError('No video frames were produced')
        with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED) as archive:
            for image in images:
                archive.write(image, image.name)
            archive.writestr('frames.json', json.dumps({'fps': edit.fps, 'start': edit.start, 'count': len(images)}))
        return {**publish(temporary, destination), 'frames': len(images), 'fps': edit.fps}
