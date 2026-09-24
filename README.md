# Notion → GitBook-style site

Turns the "Advanced Engineering Electives" page tree in your Avenues Robotics
Notion workspace into a static, multi-page documentation site with a sidebar
nav (like the GitBook example you were looking at), automatically including
only whatever you've currently published to the web in Notion.

You don't hand-pick pages here. Publish (or unpublish) pages in Notion the
normal way, then rebuild the site; the sidebar and page list always match
the current publish state.

## How it decides what's on the site

Every Notion page (and every database row, since your Projects and Content
Library entries are database rows) has a `public_url` that's only set once
you've used Notion's own Share → Publish on it. This script walks the whole
tree under your root page and includes exactly the pages/rows where that's
set. A page that *isn't* published itself but has a published page nested
under it (e.g. "Reflections and Engineering Notebook" over "Initial
Reflection") still shows up in the sidebar as a plain section label, just so
its published child is reachable, but it never gets its own page.

## Two ways to run this

**Option A: GitHub Actions (recommended).** The rebuild runs on GitHub's
servers, triggered by a "Run workflow" button in your repo's Actions tab
(reachable from your phone, no computer or Python install needed after
setup) and optionally on a schedule too. Nothing lives on your machine.

**Option B: run it locally.** A script you run yourself with Python
installed, using `run_mac.command` / `run_windows.bat` as the "button."
Useful if you'd rather not put your Notion token into GitHub at all, or want
to see the build output before it goes anywhere.

Both read the same `build_site.py` / `walker.py` / `render.py` - pick one,
or set up both if you want a local dry-run option alongside the automated
one.

---

## One-time setup (both options)

### 1. Create a Notion integration

Go to <https://www.notion.so/my-integrations>, click "New integration", give
it a name (e.g. "Site Builder"), and make sure it's an **internal**
integration with `Read content` capability. Copy the integration secret it
shows you - this is `NOTION_TOKEN`.

### 2. Share your page tree with the integration

Open "Advanced Engineering Electives" in Notion, use the `•••` menu →
"Connections" (or "Add connections"), and add the integration you just
created. Sharing a parent page shares everything underneath it too, so this
one step covers Program Overview, ME1, SCS1, Content Library, Projects, etc.

### 3. Get the root page's ID

Open "Advanced Engineering Electives" in a browser and copy its URL - the ID
is embedded in it, and the tool extracts it automatically, so you can just
paste the whole URL wherever `ROOT_PAGE_ID` is asked for.

### 4. Create the GitHub Pages repo

Pick one:

- **User/org site** - a repo literally named `<your-github-username>.github.io`.
  Serves at the domain root; leave `BASE_PATH` blank.
- **Project site** - any other repo name. Serves at
  `https://<username>.github.io/<repo>/`; set `BASE_PATH=/<repo-name>`.

Push this whole project (everything in this folder, including
`.github/workflows/build-and-deploy.yml`) into that repo.

---

## Option A: GitHub Actions setup

### 5a. Turn on Actions-based Pages

In the repo: Settings → Pages → "Build and deployment" → Source →
**GitHub Actions**. (One-time; you're telling GitHub that a workflow, not a
branch, will supply the site content.)

### 6a. Add your Notion token as a secret

Settings → Secrets and variables → Actions → "New repository secret":

- Name: `NOTION_TOKEN`
- Value: the integration secret from step 1.

This is the only thing that needs to stay secret - it never appears in the
workflow file or anywhere else in the repo.

### 7a. Fill in the non-secret settings

Open `.github/workflows/build-and-deploy.yml` and edit the three lines near
the top under `env:`:

```yaml
env:
  ROOT_PAGE_ID: "paste the URL from step 3 here"
  SITE_TITLE: ""        # optional - blank uses the root page's own title
  BASE_PATH: ""          # blank for a user/org site, "/<repo-name>" for a project site
```

Commit and push that change.

### 8a. Run it

Repo → Actions tab → "Build and deploy site from Notion" → "Run workflow".
That's the button. It builds the site (only currently-published Notion
pages) and deploys it straight to GitHub Pages, no git history of generated
HTML, no local Python required.

Check the run's logs if anything looks off - the build step prints the same
`[PUBLISHED]` / `[-]` / `[section]` tree the local dry-run does, so you can
see exactly what got included.

### Optional: rebuild on a schedule

Uncomment the `schedule:` block near the top of the workflow file (cron is
in UTC) if you want it to also refresh itself automatically, e.g. nightly,
without you clicking anything. Worth watching a few manual runs first before
turning this on.

---

## Option B: run it locally

### 5b. Install Python dependencies

```
pip install -r requirements.txt
```

(Python 3.10+ recommended.)

### 6b. Configure

```
cp .env.example .env
```

Edit `.env`:

- `NOTION_TOKEN` - the integration secret from step 1.
- `ROOT_PAGE_ID` - the URL from step 3.
- `OUTPUT_DIR` - your GitHub Pages repo clone (or its `docs` subfolder, if
  you set Pages to deploy from `/docs` instead of using Option A).
- `BASE_PATH` - as in step 4.
- `SITE_TITLE` - optional.

`.env` is gitignored - it will never get committed. Don't share it or paste
its contents anywhere; anyone with `NOTION_TOKEN` can read everything you've
shared with that integration.

### 7b. Use it

```
python3 build_site.py --dry-run     # preview what would be included, writes nothing
python3 build_site.py               # build into OUTPUT_DIR, don't push
python3 build_site.py --deploy      # build, then git add/commit/push OUTPUT_DIR
```

Once `.env` is set up, double-clicking `run_mac.command` (Finder) or
`run_windows.bat` (Explorer) does the build-and-deploy step for you.

Note: if Option A is also set up with Pages "Source: GitHub Actions", the
git-push in `--deploy` won't do anything useful (Actions-based Pages ignores
regular commits) - use one option or the other for actually deploying;
Option B's `--dry-run` and plain build are still handy for local previews
either way.

---

## Notes / gotchas

- **Rate limits.** Notion allows roughly 3 requests/second; the script paces
  itself to stay under that, so a full rebuild of a workspace this size takes
  well under a minute, but a much larger one will take longer.
- **Very long pages.** Notion's markdown export truncates pages beyond
  roughly 20,000 blocks. The build log flags any page this happens to; none
  of your current content is anywhere near that size.
- **Database entries look like pages.** Your `Projects` and `Content Library`
  databases are walked the same way as regular sub-pages - publish a project
  or a reading the normal way in Notion and it'll appear on the site, nested
  under a "Projects" / "Content Library" section label.
- **Notion formatting is converted by `notion_md.py`.** Notion's markdown
  export isn't standard markdown (callouts, toggles, columns, colored text,
  tab-nested blocks, etc.), so it has its own converter instead of a markdown
  library. If a Notion block type renders wrong, that's the file to extend;
  `python3 tests/test_notion_md.py` covers it offline.
- **Images and files uploaded to Notion.** Notion serves those from temporary
  signed URLs that expire after about an hour, so an uploaded image on the
  generated site will break shortly after a build. Use external image URLs
  (or embed links) for anything that needs to stay visible.
- **The site never edits Notion.** This only reads. It can't publish or
  unpublish anything for you - that part still happens in Notion itself.
