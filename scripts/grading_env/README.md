# Grading-host R environment

Three evaluators run `Rscript` on the host: `r_syntax`, `r_function_probe`
(sources the solver's R file and calls it with held-out arguments) and
`shiny_app_check`. Point `OSCI_RSCRIPT` in `.env` at an R 4.4.3 with the
packages the tasks expect. The most faithful option is the same lock the
guest uses:

```bash
curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj bin/micromamba
./bin/micromamba create -y -p ./envs/rstat --file scripts/vm_prep/stat/statenv.lock
echo "OSCI_RSCRIPT=$(pwd)/envs/rstat/bin/Rscript" >> .env
```

Solver-written R runs on the grading host with a 30-minute ceiling. Grade
untrusted submissions in a container.
