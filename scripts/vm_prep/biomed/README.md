# biomed: the `ubuntu_biomed` image (structural biology and NMR)

Eight tasks run on `ubuntu_biomed`: pairwise superposition and L858R mutation
modelling in PyMOL, kinase-domain extraction from three AlphaFold DB models, a
faceted RCSB search, a two-block drug-synergy audit on SynergyFinder, and a
¹H NMR assignment in Mnova (MestReNova). The image has not been built yet; the
QuPath tasks of this domain use the separate `ubuntu_qupath` image.

## What the image needs

* OSWorld base image plus open-source PyMOL (`apt install pymol`) for the
  superposition and mutation tasks.
* A browser and outbound network access: the tasks fetch from RCSB, AlphaFold DB
  and SynergyFinder while they run.
* Network access at staging time as well: the NMR task downloads the Mnova
  installer from mestrelab.com and installs it with apt, so Mnova is not part
  of the image and the image needs no licence.

## Mnova licence (NMR task only)

Mnova will not open Bruker data until a licence validates. A licence is issued
to one institution, so none is published, neither here nor in the dataset.
Supply your own:

1. Put your `.lic` file(s) in `scripts/vm_prep/biomed/mnova_licenses/`, or set
   `OSCI_MNOVA_LICENSE_DIR` to a directory that holds them. Both are ignored by
   git (`*.lic` is ignored repository-wide).
2. The pre-task hook `install_mnova_license.py` (declared in
   `configs/snapshots.yaml` for tasks whose `related_apps` contain `mnova`)
   copies them to `/home/user/licenses/` in the guest before staging. The task
   instruction tells the agent to import the NMR licence from that folder.
3. A floating (campus or site) licence also needs the guest to reach its
   licence server. If `scripts/vm_prep/biomed/mnova_site.sh` exists (ignored by
   git), the hook copies it into the guest and runs it as root after the
   licences are in place. For a server reached through a relay on the Docker
   host, for example:

   ```sh
   echo "172.18.0.1 license-server.example.edu" >> /etc/hosts
   ```

Without a licence file the hook fails and the NMR cell stops before staging;
the other seven tasks do not trigger the hook.
