# CLAUDE.md

This repo is a single-command tool: `greenscreen_bg.py INPUT "PROMPT"` replaces a green screen
in a photo or video with an AI background generated on Comfy Cloud.

When a user gives you footage and a background prompt, follow the steps in the
"Using it from Claude Code" section of README.md: install requirements, run
`python greenscreen_bg.py --check`, then run the script on their file.

- The API key comes from the `COMFY_CLOUD_API_KEY` environment variable. Never ask for it in chat or write it to a file.
- For videos, the prompt should describe only the place, not a person (see README "Prompting").
- Test inputs and outputs (images, videos) don't belong in the repo; `.gitignore` excludes them.
