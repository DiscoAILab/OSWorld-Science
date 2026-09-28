# Documentation

**User guide** — running the benchmark as shipped.

* [requirements.md](user_guide/requirements.md) — what the host needs (Linux, KVM, Docker, RAM/disk, ports, R) and what is not supported; `osci doctor`.
* [running.md](user_guide/running.md) — one cell by hand, sweeps, the run directory, ports and parallelism, options, guest lifecycle commands.
* [adding-a-task.md](user_guide/adding-a-task.md) — the task JSON, ground truth, evaluators, grader tests.

**Developer guide** — extending the benchmark.

* [adding-a-domain.md](developer_guide/adding-a-domain.md) — snapshot entry, image, tasks, data, evaluators, grader tests, hooks; what is optional.
* [adding-an-agent.md](developer_guide/adding-an-agent.md) — the agent protocol and registry.
* [building-a-vm-snapshot.md](developer_guide/building-a-vm-snapshot.md) — from the OSWorld base image to a baked, verified, published snapshot.
* [domain_integration_todos/](developer_guide/domain_integration_todos/README.md) — hand-off notes per domain (in Chinese): what of each delivered task package is integrated, what is missing, how to bring a domain's VM snapshot into the repo, and a checklist for the domain owner.
