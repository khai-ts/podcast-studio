# greenscreen-ai-background

Replace a green screen with a realistic AI-generated background, in one command.
Works on photos and videos. Generation runs on [Comfy Cloud](https://cloud.comfy.org)
(Flux.1 Fill); keying and compositing run locally with OpenCV and ffmpeg.

```bash
python greenscreen_bg.py my_clip.mp4 "a podcast studio in a penthouse overlooking New York City at night"
```

Output: `my_clip_bg.mp4` (or `_bg.png` for photos) next to the input, plus the
generated background as `my_clip_bg_plate.png`.

---

## Using it from Claude Code

Paste the repo link, your footage and a prompt, for example:

```
https://github.com/khai-ts/podcast-studio  C:\Videos\take3.mp4  cozy studio with warm lamps and bookshelves
```

> **Claude, if you were given this link:** follow these steps exactly.
>
> 1. `git clone https://github.com/khai-ts/podcast-studio ~/greenscreen-ai-background`, or `git pull` in that folder if it already exists.
> 2. `python -m pip install -r requirements.txt` inside it.
> 3. Run `python greenscreen_bg.py --check`. If the key check fails, stop and tell the user to set
>    the `COMFY_CLOUD_API_KEY` user environment variable (Comfy Cloud → Settings → API keys;
>    a paid subscription is required). Don't ask for the key in chat.
> 4. If the input is a **video** and the prompt describes a person ("a man sitting in…"),
>    rewrite it to describe only the place, and tell the user you did. See *Prompting* below.
> 5. Run `python greenscreen_bg.py "<input path>" "<prompt>"` with the user's path (quoted) and prompt.
>    Image jobs take ~30 s; video takes ~30 s of generation plus local compositing.
> 6. Report the output path, the printed seed, and show the user the result
>    (for video, extract a frame with `ffmpeg -ss 1 -i OUT -frames:v 1 preview.png`).
>
> Flags: `-o OUTPUT` sets the output path, `--seed N` reproduces a previous background.
> To try another variation, re-run without `--seed`.

---

## Setup (manual)

1. Python 3.10+ and [ffmpeg](https://ffmpeg.org/download.html) on PATH (ffmpeg is needed for video only).
2. `pip install -r requirements.txt`
3. Set a `COMFY_CLOUD_API_KEY` environment variable with your Comfy Cloud API key.
   On Windows it can be a user variable; the script reads it from the registry even in
   terminals opened before you set it.
4. `python greenscreen_bg.py --check`

## How it works

1. **Mask:** the green is keyed out of one frame (the middle frame for video).
2. **Fill:** Flux.1 Fill paints a new scene into the masked area *around* you, so the
   light and perspective fit your pose.
3. **Clean plate (video only):** a second Flux Fill pass paints you out of the scene,
   using your footprint across 12 frames, so you don't show up twice when you move.
4. **Composite:** every original frame is keyed with soft edges and green-spill removal,
   then laid over the background. Audio is kept.

The script adds "photorealistic photograph, natural lighting, shot on a cinema camera" to the prompt.

## Prompting

- **Photos:** describe the whole picture, you included ("a man standing in a penthouse with a
  view of New York City"). That helps the lighting fit.
- **Videos:** describe only the place ("a cozy studio with warm lamps and bookshelves").
  A person in the prompt tends to get painted into the background plate as a second person.

## Limits

- The background is one still image: use a locked-off camera (tripod).
- It is generated at ~1 megapixel and upscaled, so it's slightly soft, like shallow depth of field.
- Keying needs an evenly lit green screen and no green clothing.
- Cost: one Flux Fill job per photo, two per video, regardless of clip length.

## License

MIT
