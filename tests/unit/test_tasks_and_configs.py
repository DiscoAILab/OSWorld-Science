"""Every shipped task definition must be structurally valid and wired to a
registered evaluator and snapshot; configs must parse; models resolve."""
from osworld_science.evaluators.registry import names
from osworld_science.llm.models import MissingCredential, ModelRegistry
from osworld_science.tasks.registry import select_tasks
from osworld_science.tasks.validate import validate_task
from osworld_science.vm.snapshots import SnapshotRegistry


def test_task_count_and_domains(taskset):
    assert taskset.domains() == ["biomed", "chem", "econ", "eeg", "geoscience", "linguistics", "physics", "stat"]
    assert len(taskset.ids("stat")) == 20
    # 2 Praat VOT + the two review-graded LING450 labs: Hoosier phonotactics (R in RKWard) and
    # fixed-recording vowel duration (Praat, RKWard, Writer)
    assert len(taskset.ids("linguistics")) == 4
    assert len(taskset.ids("biomed")) == 34 and len(taskset.ids("chem")) == 53  # 23 QuPath + 8 structural biology/NMR + 3 radiology; retro/struct/lenacapavir + 10 atropo_*
    assert len(taskset.ids("econ")) == 6                                        # gretl + LibreOffice Calc econometrics (ECON-*)
    assert len(taskset.ids("eeg")) == 10                                      # EEGLAB terminal-science workflows
    assert len(taskset.ids("geoscience")) == 6                                  # six QGIS remote sensing tasks
    assert len(taskset.ids("physics")) == 31  # 14 OpenFOAM OF-xxx + 3 CIAO/DS9 chandra-* + 14 Ansys Fluent FT/FV-xxx
    # the 162 of the published dataset (two of them linguistics) + the local-only linguistics labs counted above
    assert len(taskset) - len(taskset.ids("linguistics")) == 160


def test_every_task_validates(taskset, settings):
    snaps = SnapshotRegistry(settings.configs_dir / "snapshots.yaml")
    problems = {t.id: validate_task(t, evaluator_names=names(), snapshot_names=set(snaps.names()))
                for t in taskset}
    assert {k: v for k, v in problems.items() if v} == {}


def test_prompt_appends_deliverables(taskset):
    t = taskset.get("stat_qol_sql")
    p = t.prompt()
    assert p.startswith(t.instruction) and "\n\nDeliverables:\n  - /home/user/work/submission/finaldata_query.sql" in p
    assert t.max_steps == 100 and t.snapshot == "ubuntu_stat"


def test_select_tasks(taskset):
    assert [t.id for t in select_tasks(taskset, "biomed")] == taskset.ids("biomed")
    assert [t.id for t in select_tasks(taskset, "stat_qol_sql, praat_vot_plosive1")] == \
        ["stat_qol_sql", "praat_vot_plosive1"]
    assert len(select_tasks(taskset, "all", domain="stat")) == 20


def test_snapshots_config(settings):
    reg = SnapshotRegistry(settings.configs_dir / "snapshots.yaml")
    s = reg["ubuntu_stat"]
    assert s.default_port == 5040 and s.provision and s.pre_task_hooks[0].when_related_app == "sas"
    assert s.pre_task_hooks[0].applies_to(["SAS", "r"]) and not s.pre_task_hooks[0].applies_to(["r"])
    assert reg.defaults.ram == "8G" and reg.defaults.boot_timeout_s == 1520
    assert s.ram is None and s.cpus is None                       # defaults apply
    qp = reg["ubuntu_qupath"]
    assert qp.ram == "24G" and qp.cpus == 8 and qp.domain == "biomed" and qp.default_port == 5060
    lic = reg["ubuntu_biomed"].pre_task_hooks[0]                   # the operator's Mnova licence
    assert lic.required and lic.when_related_app == "mnova"
    assert lic.applies_to(["mnova", "terminal"]) and not lic.applies_to(["pymol", "chrome"])
    assert reg["ubuntu_astro"].domain == "physics"
    assert reg["ubuntu_eeglab"].domain == "eeg" and reg["ubuntu_eeglab"].default_port == 5150
    assert reg["ubuntu_eeglab"].provision is None                  # scripts/vm_prep/eeg/provision_guest.sh runs inside the guest; nothing drives it from the host
    econ = reg["ubuntu_econ"]                                      # image ships with the data; the provisioner covers a base image booted in its place
    assert econ.domain == "econ" and econ.image == "ubuntu_econ.qcow2"
    assert econ.provision == "scripts/vm_prep/econ/provision_guest.sh" and (settings.repo_root / econ.provision).is_file()
    ports = [reg[n].default_port for n in reg.names()]
    assert econ.default_port == 5170 and len(ports) == len(set(ports))   # no two snapshots share a default port
    assert reg["ansys_win10_fluent2026r1"].domain == "physics"


def test_models_config_resolves_with_fake_env(settings):
    reg = ModelRegistry(settings.configs_dir / "models.yaml")
    env = {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "https://x.example", "OPENROUTER_API_KEY": "k",
           "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://a.example",
           "GEMINI_API_KEY": "k", "QWEN_API_KEY": "k", "QWEN_BASE_URL": "https://q.example",
           "MOONSHOT_API_KEY": "k"}
    m = reg.resolve("gpt-5.6-terra", env)
    # backends carry the API root; the agent's `api` adds the path
    assert m.base_url == "https://x.example/openai/v1" and m.backend.max_tokens_field == "max_completion_tokens"
    assert m.endpoint("chat_completions") == "https://x.example/openai/v1/chat/completions"
    assert m.endpoint("responses") == "https://x.example/openai/v1/responses"
    assert m.openai_client_kwargs()["default_headers"] == {"api-key": "k"}       # Azure's v1 route
    assert reg.resolve("minimax-m3", env).base_url == "https://openrouter.ai/api/v1"
    assert reg.resolve("qwen3.7-plus", env).endpoint() == "https://q.example/chat/completions"
    claude = reg.resolve("claude-opus-5", env)
    assert claude.endpoint("responses") == "https://a.example/v1/messages"       # Messages whatever the api
    assert "default_headers" not in claude.openai_client_kwargs()
    # an old .env that still names the chat endpoint keeps working
    assert reg.resolve("qwen3.7-plus", {**env, "QWEN_BASE_URL": "https://q.example/chat/completions/"}).base_url \
        == "https://q.example"
    kimi = reg.resolve("kimi-k3", env)
    assert kimi.backend.name == "moonshot" and kimi.base_url == "https://api.moonshot.ai/v1"
    assert kimi.endpoint() == "https://api.moonshot.ai/v1/chat/completions"
    try:
        reg.resolve("claude-opus-5", {})
    except MissingCredential as e:
        assert "ANTHROPIC_API_KEY" in str(e)
    else:
        raise AssertionError("missing key must raise")


def test_models_declare_their_agents(settings):
    """Every model lists the harnesses it runs under and a default among them."""
    reg = ModelRegistry(settings.configs_dir / "models.yaml")
    for m in reg.models.values():
        assert m.agents and m.default_agent in m.agents, m.name
    kimi, alt = reg.get("kimi-k3"), reg.get("kimi-k3@openrouter")
    assert kimi.agents == ("kimi",) and kimi.default_agent == "kimi"
    assert alt.agents == ("kimi",) and alt.provider_order == ("moonshotai",) and alt.backend.name == "openrouter"
    for name in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5-1"):
        assert reg.get(name).agents == ("prompt", "claude-cua")        # no Responses API: no openai-cua
    assert set(reg.get("gpt-5.6-sol").agents) == {"prompt", "openai-cua", "codex"}
    for name, spec in reg.models.items():                       # codex needs a backend that serves /responses
        assert ("codex" in spec.agents) <= (spec.backend.name in ("azure", "openrouter")), name
        assert spec.default_agent != "codex", name                # opt-in through --agent, never the default
    assert reg.get("gpt-5.6-sol").supports_agent("openai-cua") and not reg.get("claude-opus-5").supports_agent("openai-cua")
    for j in reg.judges.values():
        assert j.agents == () and j.default_agent == ""                  # judges are never a harness
