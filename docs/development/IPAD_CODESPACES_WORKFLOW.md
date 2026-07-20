# iPad-only GitHub and Codespaces workflow

No PC is required for the normal Roadplanner development and deployment workflow.

## One-time bootstrap of the empty repository

1. In the empty GitHub repository, choose **uploading an existing file**.
2. Upload the provided Roadplanner bootstrap ZIP and commit it to `main`.
3. Open **Code → Codespaces → Create codespace on main**.
4. In the Codespaces terminal run:

```bash
unzip Roadplanner_GitHub_Bootstrap_2.6.5_to_3.0.zip -d /tmp/roadplanner-bootstrap
cp -a /tmp/roadplanner-bootstrap/. .
rm Roadplanner_GitHub_Bootstrap_2.6.5_to_3.0.zip
python tools/validate_repository.py
git add .
git commit -m "chore: import Roadplanner 2.6.5 baseline and 3.0 foundation"
git push
```

5. Stop the Codespace when finished.

## Normal editing

For small Markdown or configuration changes, use the free `github.dev` browser editor:

- open the repository,
- replace `github.com` with `github.dev`, or press `.` with a keyboard,
- edit and commit.

For code changes, tests, ZIP extraction or release building, use a Codespace.

## Branches

- `main`: tested and releasable.
- `develop`: active Roadplanner 3.x development.
- optional short feature branches only for larger changes.

Create `develop` after the baseline is imported.

## HACS deployment

HACS requires the GitHub repository to be public. Keep the repository private while importing and auditing it. After selecting a license and verifying that no personal data or secrets are present:

1. switch the repository to public,
2. create a GitHub release,
3. add the repository URL to HACS as a custom repository of type **Integration**.
