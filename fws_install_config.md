There are really two different setups here, and mixing them is what caused the low scores:

- For the PinchBench `gws`/`gh` tasks, you want the local mock server `fws`, not your real Google/GitHub accounts. That’s what the task docs expect in [task_gws_email_triage.md](/opt/docker-amd/skill/tasks/task_gws_email_triage.md:147) and [task_gh_issue_triage.md](/opt/docker-amd/skill/tasks/task_gh_issue_triage.md:141), and the runner is already written to start `fws` automatically if it exists in [lib_agent.py](/opt/docker-amd/skill/scripts/lib_agent.py:826).
- For everyday OpenClaw usage, you can also set up real `gws-axi` OAuth and real `gh` auth.

Your current machine state is:
- `gws-axi` is installed at `/home/intel/.local/bin/gws-axi`
- `gh` is installed, but `gh auth status` says not logged in
- `fws` is not installed on `PATH`
- `gws-axi auth status` is `incomplete (1/7)` with `next_step: gcp_project`

**Recommended For Benchmark Tasks**
Use this first for the 4 low-score tasks.

1. Install `fws`:
```bash
npm install -g @juppytt/fws
```

2. Verify it:
```bash
fws --help
```

3. Make sure `gws` exists as a command.
Right now you have `gws-axi`, but the tasks and agents expect `gws`.
```bash
ln -sf /home/intel/.local/bin/gws-axi /home/intel/.local/bin/gws
hash -r
which gws
gws --help
```

4. Start the mock server and load its env:
```bash
fws server start
eval "$(fws server env)"
```

5. Also give `gh` a token-shaped env var in that same shell.
This is an inference based on how `gh` behaves locally: it likely only needs a token present once `fws` is intercepting the API.
```bash
export GH_TOKEN=fake
```

6. Confirm the env is live:
```bash
env | grep -E 'GOOGLE_WORKSPACE_CLI|HTTPS_PROXY|SSL_CERT_FILE|GH_TOKEN'
```

7. Smoke-test both CLIs in the same shell:
```bash
gws doctor
gh issue list -R testuser/my-project
gh pr list -R testuser/my-project
```

8. Run OpenClaw or PinchBench from that same shell. Select the integration tasks explicitly so the command does not depend on a run-specific helper:
```bash
MODEL="<model-id>"
SUITE="task_gh_issue_triage,task_gws_email_triage,task_gws_cross_service,task_gws_task_management"
./scripts/run.sh \
  --model "$MODEL" \
  --suite "$SUITE" \
  --no-upload
```

9. If you use raw OpenClaw CLI manually, prefer local mode so env propagates:
```bash
openclaw agent --local --agent main --message "Use gh to list issues in testuser/my-project"
```


The benchmark/mock setup is now in place for this environment.

I installed `fws`, installed the benchmark-compatible `gws` CLI from `@googleworkspace/cli`, kept `gws-axi` available separately, and added a reusable helper at [setup_mock_benchmark_env.sh](/opt/docker-amd/skill/scripts/setup_mock_benchmark_env.sh). That helper starts `fws` and exports the mock env PinchBench expects, including `GH_TOKEN=fake` and `GH_REPO=testuser/my-project`.

I also verified the key paths work:
- `gh issue list` and `gh pr list` return the seeded GitHub repo items
- `gws gmail users messages list --params '{"userId":"me"}'` returns the seeded Gmail messages
- `gws drive files list` returns the seeded Drive files

Use this in the shell where you want to run OpenClaw or PinchBench:

```bash
source /opt/docker-amd/skill/scripts/setup_mock_benchmark_env.sh
```

Then rerun the four tasks from the repository root with:

```bash
MODEL="<model-id>"
SUITE="task_gh_issue_triage,task_gws_email_triage,task_gws_cross_service,task_gws_task_management"
./scripts/run.sh \
  --model "$MODEL" \
  --suite "$SUITE" \
  --no-upload
```

I did not start the rerun yet, so the environment is ready without consuming another benchmark run.

**Important OpenClaw Note**
You probably do not need to edit [openclaw.json](/home/intel/.openclaw/openclaw.json:1) for any of this. The key thing is which environment variables are present when OpenClaw or PinchBench starts. For the benchmark tasks, `fws` plus the shell env matters more than OpenClaw config.


**Appendix:**

**Real Google Workspace Setup**
This is for actual Gmail/Calendar/Drive use, not the benchmark mocks.

1. Install OpenClaw hooks so sessions show GWS state:
```bash
gws-axi setup hooks
```

2. Start guided setup:
```bash
gws-axi auth setup
```

3. If you already have a Google OAuth client JSON, use:
```bash
gws-axi auth join /path/to/credentials.json
```

4. Otherwise keep using:
```bash
gws-axi auth setup
```
until the missing steps are done. Your current blocker is `gcp_project`.

5. Authenticate an account:
```bash
gws-axi auth login --account you@example.com
```

6. Verify:
```bash
gws-axi auth accounts
gws-axi auth status
gws-axi doctor
```

7. If needed, choose the default account:
```bash
gws-axi auth use you@example.com
```

**Real GitHub Setup**
This is for actual GitHub repos.

1. Browser login:
```bash
gh auth login
```

2. Or token-based login:
```bash
gh auth login --with-token < ~/your-token.txt
```

3. Or for headless/OpenClaw shells:
```bash
export GH_TOKEN=your_token_here
```

4. Verify:
```bash
gh auth status
gh repo view owner/repo
```
