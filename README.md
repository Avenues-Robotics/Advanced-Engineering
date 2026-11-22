# Advanced Engineering

| Folder | What it is |
| --- | --- |
| [`course_site_builder/`](course_site_builder/) | Builds the course website from Notion and deploys it to GitHub Pages. See its [README](course_site_builder/README.md). |

`.github/workflows/` stays at the top level because GitHub only looks for
workflows there. The site workflow runs inside `course_site_builder/`.
