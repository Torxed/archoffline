#!/usr/bin/python

try:
	# Try submodule-import
	from archinstall import archinstall
except ImportError:
	# Try system-package import
	import archinstall

import typing
import dataclasses
import glob
import logging
import os
import pathlib
import re
import shutil
import stat
import urllib.request

if archinstall.arguments.get('help', None):
	print(f"""
This is a helper script to create Arch Linux ISO's that support
Offline installations. It requires root privileges due to archiso itself.

Usage:

	sudo python offline.py

Arguments:

	--template=<archiso template name>
	  Defines which archiso template to adapt into a offline version.
	  The default is: releng

	--mirrors=<region>
	  Uses the current HTTP/HTTPS enabled mirrors from this region.
	  Optional magical keywords instead of a region are:
	   * copy - copies the /etc/pacman.conf setup to the build env
	   * https://... - Will hard-code a specific repo server
	   * file:// - Will use a locally stored mirror as the build env repo

	--packages="<list of packages>"
	  A space separated list of packages to ship with the iso
	  aside from the default packages in archiso packages.x86_64

	--rebuild
	  Cleans and re-creates the builddir and other dependencies

	--breakpoint
	  Creates a breakpoint right before mkarchiso is executed.
	  This way you can do manual changes to the ISO layout before
	  mkarchiso is executed.

	--repo=<repo name>
	  You can override the repository name created inside the ISO.
	  The default is: localrepo

	--builddir=<path to folder>
	  You can override the build directory for the ISO.
	  The default is: ./archiso_offline/

	--verbose
	  Enables printout for all the syscalls that are being made.
	  (For instance output from mkarchiso)

	--boot
	  Boots the built ISO, either after --rebuild or old build.

	--archinstall
	  Clones in archinstall with the branch given on --ai-branch in /root/archinstall-git.
	  (This is optional, archinstall stable is shipped as a package already)

	--ai-branch=<archinstall branch to clone>
	  This can override the default `master` branch.

	--profiles=[a commaseparated list of profile paths]
	  If a profile is given, it is copied into archinstall master directory.
	  This option implies --archinstall is given.

	--aur-packages="<list of AUR packages>"
	  A space separated list of AUR packages that will be built and
	  shipped within the ISO mirror as a standard package.

	--aur-user=username
	  Set a build-user username, if this user does not exist it will be
	  created. If the user does not have an entry in /etc/sudoers an entry
	  will be created with NOPASSWD only if the user is locked on the local system.
	  (the AUR user will be deleted as well as the /etc/sudoers entry unless pre-configured)

	--resources="list of assets/resource URL's"
	  A semi-colon (;) separated list of resources to package into the ISO.
	  The resources will be stored in /root/resources. Acceptable URL's examples:
	  https://
	  /local/path.txt
	  git://
	  https://.git  (note: trailing .git important to trigger git clone)

	--customize=<path to script>
	  Add a script that will be run from within Archiso and executed before
	  the ISO is finalized.

	--autorun-archinstall
	  This will auto-launch archinstall when the ISO boots.
	  This requires --archinstall to be given as well and is not impllied.
	  autorun will also re-install archinstall to the latest version against --ai-branch

	--silent
	  Does not prompt for anything, will skip by default or error out if key parameters
	  were not found during execution.

Examples:

	sudo python offline.py --mirrors=Sweden --packages="nano wget" --rebuild
""")
	# 	--pxe
	#     This flag will be deprecated in the future, but this will fix some PXE issues
	#     with the generated ISO, such as timeout for DHCP configuration and signature
	#     of the airootfs which might not be deisrable.
	#     This is done by replacing `ipconfig -t 20 ...` with `ipconfig -t 60 ...` and
	#     to set `verify=f` in the build of mkinitcpio.
	exit(0)

REPO_NAME=archinstall.arguments.get('repo', 'localrepo')
BUILD_DIR=pathlib.Path(archinstall.arguments.get('builddir', './archiso_offline/')).absolute().resolve()
PACMAN_TEMPORARY_BUILD_DB = pathlib.Path(f'{BUILD_DIR}/tmp.pacdb/').absolute().resolve()
PACMAN_CACHE_DIR = pathlib.Path(f'{BUILD_DIR}/airootfs/root/{REPO_NAME}/').absolute().resolve()
PACMAN_SYNC_CONF = f'{BUILD_DIR}/pacman.sync.conf' # Used to sync packages to localrepo
PACMAN_BUILD_CONF = f'{BUILD_DIR}/pacman.build.conf' # Used to sync packages to localrepo

def download_file(url, destination, filename=""):
	if not (dst := pathlib.Path(destination)).exists():
		dst.mkdir(parents=True)
	if dst.is_file():
		return False

	tmp_filename, headers = urllib.request.urlretrieve(url)
	shutil.move(tmp_filename, f"{destination}/{filename}")
	return True

@dataclasses.dataclass
class PackageListing:
	_inventory :list = dataclasses.field(default_factory=list)

	def __add__(self, obj):
		if type(obj) != PackageListing:
			raise ValueError(f"PackageListing requires addition object to be of PackageListing() too")

		self._inventory += obj._inventory
		return self

	def __len__(self):
		return len(self._inventory)

	@property
	def inventory(self):
		return self._inventory

	@inventory.setter
	def inventory(self, value):
		if type(value) != list:
			raise ValueError(f"Inventory of a PackageListing() must be a list containing strings.")

		if any(True for val in value if type(val) != str):
			raise ValueError(f"Inventory items must be of type str [a-Z0-9-_]+")

		if archinstall.arguments.get('skip-validation', False) is not False:
			archinstall.log(f"Validating packages: {value}", level=logging.INFO)
			try:
				archinstall.validate_package_list(value)
			except archinstall.RequirementError as e:
				archinstall.log(f"==> {e}", fg='red', level=logging.ERROR)
				exit(1)

		self._inventory = value

class BobTheBuilder():
	_build_dir = BUILD_DIR # Copy the values from the global conf and freeze them
	_pacman_sync_conf = PACMAN_SYNC_CONF
	_pacman_build_conf = PACMAN_BUILD_CONF
	_pacman_temporary_database :pathlib.Path = PACMAN_TEMPORARY_BUILD_DB
	_pacman_package_cache_dir :pathlib.Path = PACMAN_CACHE_DIR
	_repo_name :str = REPO_NAME

	def __init__(self):
		self._packages :PackageListing = PackageListing()
		self._aur_packages :PackageListing = PackageListing()

	@property
	def packages(self):
		return self._packages.inventory

	@packages.setter
	def packages(self, value):
		self._packages.inventory = value

	@property
	def aur_packages(self):
		return self._aur_packages.inventory

	@aur_packages.setter
	def aur_packages(self, value):
		self._aur_packages.inventory = value

	def sanity_checks(self):
		if os.getuid() != 0:
			archinstall.log(f"==> Permission error, needs to be run as {archinstall.stylize_output('root', fg='red')}", level=logging.ERROR)
			exit(1)

		try:
			archinstall.SysCommand('pacman -Q archiso')
		except archinstall.SysCallError:
			archinstall.log(f"==> Missing requirement: {archinstall.stylize_output('archiso', fg='red')}", level=logging.ERROR)
			exit(1)

	def move_folder(self, source, destination, force=False):
		if not source.exists():
			return True

		if destination.exists() and force is False:
			if archinstall.arguments.get('silent', None):
				archinstall.log(f"==> Backing up {source} but destination {destination} already existed.", level=logging.WARNING, fg="orange")
			else:
				archinstall.log(f"==> Backing up {source} but destination {destination} already exists.", level=logging.ERROR, fg="red")
				exit(1)

		if destination.exists():
			shutil.rmtree(destination, ignore_errors=False)

		shutil.move(source, destination)
		archinstall.log(f"==> Backed up {source} to {destination}.", level=logging.ERROR, fg="green")

	def clean_old_build_information(self):
		if self._build_dir.exists():
			shutil.rmtree(f"{self._build_dir}")

	def create_build_dir_for_conf(self, archiso_configuration):
		archinstall.log(f"==> Ensuring the Arch ISO configuration {archinstall.stylize_output(archiso_configuration, fg='teal')} build dir {archinstall.stylize_output(self._build_dir, fg='teal')} is setup properly.", level=logging.INFO)
		for obj in glob.glob(f'/usr/share/archiso/configs/{archiso_configuration}/*'):
			if (self._build_dir / obj.split('/')[-1]).exists() is False:
				if os.path.isdir(obj):
					shutil.copytree(obj, f"{self._build_dir}/{obj.split('/')[-1]}", symlinks=True)
				else:
					shutil.copy2(obj, f"{self._build_dir}/{obj.split('/')[-1]}")

		self._pacman_temporary_database.mkdir(parents=True, exist_ok=True)

	def disable_reflector(self):
		reflector_config = self._build_dir/"airootfs"/"etc"/"systemd"/"system"/"reflector.service.d"/"archiso.conf"
		reflector_user_config = self._build_dir/"airootfs"/"usr"/"lib"/"systemd"/"system"/"reflector.service"
		if reflector_config.exists():
			archinstall.log(f"==> Removed reflector service from ISO build", level=logging.INFO, fg="green")
			reflector_config.unlink()
		else:
			if archinstall.arguments.get('rebuild', None) is not None:
				archinstall.log(f"==> Could not remove {str(reflector_config).replace(str(self._build_dir), '')}", level=logging.WARNING, fg="red")
		
		if reflector_user_config.exists():
			archinstall.log(f"==> Removed reflector service for users from ISO build", level=logging.INFO, fg="yellow")
			reflector_user_config.unlink()
		else:
			if archinstall.arguments.get('rebuild', None) is not None:
				archinstall.log(f"==> Could not remove {str(reflector_user_config).replace(str(self._build_dir), '')} (usually ok, as long as previous step worked)", level=logging.WARNING, fg="gray")

	def apply_offline_patches(self):
		self.disable_reflector()
		archinstall.log(f"==> Applied offline patches.", level=logging.INFO, fg="green")

	def get_mirrors_from_archinstall(self):
		archinstall.log(f"==> Getting current mirror list from archinstall.", level=logging.INFO, fg="gray")
		if not (mirror_region_data := archinstall.arguments.get('mirrors', None)):
			mirror_region_data = archinstall.select_mirror_regions(archinstall.list_mirrors())
			if not mirror_region_data:
				raise archinstall.RequirementError("A mirror region is required. Future versions will source /etc/pacman.d/mirrors.")

			mirrors = list(list(mirror_region_data.values())[0].keys())
		else:
			mirror_region_data = archinstall.list_mirrors()[mirror_region_data]
			mirrors = list(mirror_region_data.keys())

		return mirrors

	def create_pacman_conf_for_sync(self, mode):
		if mode == 'copy':
			with open('/etc/pacman.conf', 'r') as source_conf:
				with open(self._pacman_sync_conf, 'w') as dest_conf:
					for line in source_conf:
						if 'DBPath' in line:
							line = f"DBPath      = {self._pacman_temporary_database}\n"
						elif 'CacheDir' in line:
							line = f"CacheDir    = {self._pacman_package_cache_dir}\n"

						dest_conf.write(line)

		else: # mode == 'new'
			with open(self._pacman_sync_conf, 'w') as pac_conf:
				# Some general pacman options to setup before we decide the specific source for the packages
				pac_conf.write(f"[options]\n")
				pac_conf.write(f"DBPath      = {self._pacman_temporary_database}\n")
				pac_conf.write(f"CacheDir    = {self._pacman_package_cache_dir}\n")
				pac_conf.write(f"HoldPkg     = pacman glibc\n")
				pac_conf.write(f"Architecture = auto\n")
				pac_conf.write(f"\n")
				pac_conf.write(f"CheckSpace\n")
				pac_conf.write(f"\n")
				pac_conf.write(f"SigLevel    = Required DatabaseOptional\n")
				pac_conf.write(f"LocalFileSigLevel = Optional\n")
				pac_conf.write(f"\n")

				archinstall.log(f"Retrieving and using active mirrors from region {mirror_region} for ISO build.", level=logging.INFO)
				mirror_str_list = '\n'.join(f"Server = {mirror}" for mirror in get_mirrors_from_archinstall())

				pac_conf.write(f"\n")
				pac_conf.write(f"[core]\n")
				pac_conf.write(f"{mirror_str_list}\n")
				pac_conf.write(f"[extra]\n")
				pac_conf.write(f"{mirror_str_list}\n")
				pac_conf.write(f"[community]\n")
				pac_conf.write(f"{mirror_str_list}\n")

		archinstall.log(f"==> Created pacman conf for building.", level=logging.INFO, fg="green")

	def load_default_packages(self):
		packages = []
		with open(f"{self._build_dir}/packages.x86_64", 'r') as packages_raw_file:
			for line in packages_raw_file:
				if line[0] == '#': continue
				if len(line.strip()) == 0: continue
				packages.append(line.strip())

		self.packages += packages
		archinstall.log(f"==> Default packages have been loaded from chosen Archiso configuration.", level=logging.INFO, fg="green")

	def package_exists(self, package_name):
		return glob.glob(str(self._pacman_package_cache_dir / f"{package_name}*.pkg*"))

	def build_aur_packages(self):
		archinstall.log(f"==> Checking/Setting up temporary AUR build environment", level=logging.INFO, fg="teal")
		if len(self._aur_packages) == 0:
			return True

		def untar_file(file):
			archinstall.SysCommand(f"/usr/bin/sudo -H -u {archinstall.arguments.get('aur-user', 'aoffline_usr')} /usr/bin/tar --directory /home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/ -xvzf {file}")

		sudo_user = archinstall.arguments.get('aur-user', 'aoffline_usr')
		try:
			found_aur_user = archinstall.SysCommand(f"id {sudo_user}").exit_code == 0
		except:
			found_aur_user = False

		found_aur_user_sudo_entry = False
		found_aur_user_sudo_entry_in_sudoers = False
		sudo_entries = []
		with open('/etc/sudoers', 'r') as fh:
			for line in fh:
				sudo_entries.append(line)
				if sudo_user in line and not line.startswith('#'):
					found_aur_user_sudo_entry = True
					found_aur_user_sudo_entry_in_sudoers = True
		
		if not found_aur_user_sudo_entry:
			found_aur_user_sudo_entry = pathlib.Path(f'/etc/sudoers.d/{sudo_user}').exists()

		if not found_aur_user:
			archinstall.log(f"==> Creating temporary build user {sudo_user}", level=logging.INFO, fg="gray")
			archinstall.SysCommand(f"/usr/bin/useradd -m -N -s /bin/bash {sudo_user}")

		if not found_aur_user_sudo_entry:
			archinstall.log(f"Creating temporary sudoers entry for user {sudo_user}")
			with pathlib.Path(f'/etc/sudoers.d/{sudo_user}').open('w') as fh:
				fh.write(f"{sudo_user} ALL=(ALL) NOPASSWD: ALL\n")

		if archinstall.arguments.get('verbose', None):
			archinstall.log(f"==> Syncronizing AUR packages: {self._aur_packages}")
		else:
			archinstall.log(f"==> Syncronizing {len(self._aur_packages)} AUR packages (this might take a while)")
		# Try:
		# error = False
		for package in self.aur_packages:
			if archinstall.arguments.get('verbose', None):
				archinstall.log(f"==> Starting build process for: {package}")

			if self.package_exists(package) and archinstall.arguments.get('rebuild', False) is False:
				if archinstall.arguments.get('verbose', None):
					archinstall.log(f"==> Package existed in cache", level=logging.INFO, fg="green")
				continue

			archinstall.log(f"Building AUR package {package}", level=logging.INFO, fg="yellow")
			if not download_file(f"https://aur.archlinux.org/cgit/aur.git/snapshot/{package}.tar.gz", destination=f"/home/{sudo_user}/", filename=f"{package}.tar.gz"):
				archinstall.log(f"Could not retrieve {package} from: https://aur.archlinux.org/cgit/aur.git/snapshot/{package}.tar.gz", fg="red", level=logging.ERROR)
				continue

			archinstall.SysCommand(f"/usr/bin/chown {sudo_user} /home/{sudo_user}/{package}.tar.gz")

			untar_file(f"/home/{sudo_user}/{package}.tar.gz")
			with open(f"/home/{sudo_user}/{package}/PKGBUILD", 'r') as fh:
				PKGBUILD = fh.read()

			# This regexp needs to accomodate multiple keys, as well as the logic below
			gpgkeys = re.findall('validpgpkeys=\(.*\)', PKGBUILD)
			if gpgkeys:
				keys=[]
				for gpgkey in gpgkeys:
					regexkeys = re.findall('[A-F0-9]{40}', gpgkey)
					for regexkey in regexkeys:
						keys.append(regexkey)
				for key in keys:
					archinstall.log(f"Adding GPG-key {key} to session for {sudo_user}")
					archinstall.SysCommand(f"/usr/bin/sudo -H -u {sudo_user} /usr/bin/gpg --recv-keys {key}")

			if (build_handle := archinstall.SysCommand(f"/usr/bin/sudo -H -u {sudo_user} /bin/bash -c \"cd /home/{sudo_user}/{package}; makepkg --clean --force --cleanbuild --noconfirm --needed -s\"", peak_output=archinstall.arguments.get('verbose', False))).exit_code != 0:
				archinstall.log(build_handle, level=logging.ERROR)
				archinstall.log(f"Could not build {package}, see traceback above. Continuing to avoid re-build needs for the rest of the run and re-runs.", fg="red", level=logging.ERROR)
			else:
				if (built_packages := glob.glob(f"/home/{sudo_user}/{package}/*.tar.zst")):
					archinstall.log(f"==> Moving package {built_packages[0]} to {self._pacman_package_cache_dir}", level=logging.INFO, fg="gray")
					if self._pacman_package_cache_dir.exists() is False:
						self._pacman_package_cache_dir.mkdir()

					for built_package in built_packages:
						shutil.move(built_package, f"{self._pacman_package_cache_dir}/")
						archinstall.SysCommand(f"/usr/bin/chown root. {glob.glob(f'{self._pacman_package_cache_dir}/{built_package}')}")
					
					shutil.rmtree(f"/home/{sudo_user}/{package}")
					pathlib.Path(f"/home/{sudo_user}/{package}.tar.gz").unlink()
				else:
					archinstall.log(f"Could not build {package}, see traceback above. Continuing to avoid re-build needs for the rest of the run and re-runs.", fg="red", level=logging.ERROR)
		# Except: safely remove the user if needed and the nexit

		if not found_aur_user:
			archinstall.log(f"Removing temporary build user {sudo_user}")
			# Stop dirmngr and gpg-agent before removing home directory and running userdel
			archinstall.SysCommand(f"/usr/bin/systemctl --machine={sudo_user}@.host --user stop dirmngr.socket") # Doesn't do anything?
			archinstall.SysCommand(f"/usr/bin/killall -u {sudo_user}")
			archinstall.SysCommand(f"/usr/bin/sudo -H -u {sudo_user} /usr/bin/gpgconf --kill gpg-agent")
			archinstall.SysCommand(f"/usr/bin/userdel {sudo_user}")
			shutil.rmtree(f"/home/{sudo_user}")

		if not found_aur_user_sudo_entry:
			if found_aur_user_sudo_entry_in_sudoers:
				archinstall.log(f"Removing temporary sudoers entry for user {sudo_user}")
				with open('/etc/sudoers', 'w') as fh:
					for line in sudo_entries:
						fh.write(line)
			else:
				pathlib.Path(f"/etc/sudoers.d/{sudo_user}").unlink()

		# If error, raise DependencyError

		archinstall.log(f"==> All AUR packages have been built successfully.", level=logging.INFO, fg="green")

	def write_packages_to_package_file(self):
		with open(f"{BUILD_DIR}/packages.x86_64", 'w') as x86_packages:
			for package in self.packages:
				x86_packages.write(f"{package}\n")

			for aur_package in self.aur_packages:
				x86_packages.write(f"{aur_package}\n")

		archinstall.log(f"==> Updated Archiso build configuration with all packages (official and AUR) before build.", level=logging.INFO, fg="green")

	def download_package_list(self):
		if archinstall.arguments.get('verbose', None):
			archinstall.log(f"==> Syncronizing packages using: pacman --noconfirm --config {self._pacman_sync_conf} -Syw {' '.join(self.packages)}")
		else:
			archinstall.log(f"==> Syncronizing {len(self.packages)} packages (this might take a while)")

		if (pacman := archinstall.SysCommand(f"pacman --noconfirm --config {self._pacman_sync_conf} -Syw {' '.join(self.packages)}", peak_output=archinstall.arguments.get('verbose', False))).exit_code != 0:
			archinstall.log(pacman, level=logging.ERROR, fg="red")
			archinstall.log(pacman.exit_code)
			exit(1)

		archinstall.log(f"==> Finished downloading all the listed packages to package cache.", level=logging.INFO, fg="green")

	def update_offline_repo_database(self):
		archinstall.log(f"==> Building offline repository database in build environment.", level=logging.INFO, fg="teal")
		
		if (repoadd := archinstall.SysCommand(f"/bin/bash -c \"repo-add {self._pacman_package_cache_dir}/{self._repo_name}.db.tar.gz {self._pacman_package_cache_dir}/{{*.pkg.tar.xz,*.pkg.tar.zst}}\"", peak_output=archinstall.arguments.get('verbose', False))).exit_code != 0:
			archinstall.log(repoadd, level=logging.ERROR, fg="red")
			archinstall.log(repoadd.exit_code)
			exit(1)

		archinstall.log(f"==> Finished updating offline repository in build environment.", level=logging.INFO, fg="green")

	def create_pacman_conf_for_build(self):
		with open(self._pacman_build_conf, 'w') as pac_conf:
			pac_conf.write(f"[options]\n")
			pac_conf.write(f"DBPath      = {self._pacman_temporary_database}\n")
			pac_conf.write(f"CacheDir    = {self._pacman_package_cache_dir}\n")
			pac_conf.write(f"HoldPkg     = pacman glibc\n")
			pac_conf.write(f"Architecture = auto\n")
			pac_conf.write(f"\n")
			pac_conf.write(f"CheckSpace\n")
			pac_conf.write(f"\n")
			pac_conf.write(f"SigLevel    = Required DatabaseOptional\n")
			pac_conf.write(f"LocalFileSigLevel = Optional\n")
			pac_conf.write(f"\n")

			
			# Local mirror options
			pac_conf.write(f"[{self._repo_name}]\n")
			pac_conf.write(f"SigLevel = Optional TrustAll\n")
			pac_conf.write(f"Server = file://{self._pacman_package_cache_dir}\n")

x = BobTheBuilder()
x.sanity_checks()

if archinstall.arguments.get('silent', False) is False:
	if packages := archinstall.arguments.get('packages', '').split():
		x.packages = packages
	else:
		archinstall.log(f"--- The parameter --packages was empty, asking user for more questions", level=logging.INFO, fg="gray")
		x.packages = input('Any additional packages to add to offline repo: ').split()

	if aur_packages := archinstall.arguments.get('aur-packages', '').split():
		x.aur_packages = aur_packages
	else:
		archinstall.log(f"--- The parameter --aur-packages was empty, asking user for more questions", level=logging.INFO, fg="gray")
		x.aur_packages = input('Any AUR packages to add to offline repo: ').split()

# Save potential cache directories to avoid network load
if archinstall.arguments.get('save-offline-repository-cache', False):
	x.move_folder(x._pacman_package_cache_dir, pathlib.Path(f"./{x._pacman_package_cache_dir.name}"))
	x.move_folder(x._pacman_temporary_database, pathlib.Path(f"./{x._pacman_temporary_database.name}"), force=True)

if archinstall.arguments.get('save-builddir-package-cache', False):
	pass

# Being build configuration
if archinstall.arguments.get('rebuild', None):
	x.clean_old_build_information()

x.create_build_dir_for_conf(archinstall.arguments.get('archiso-conf', 'releng'))
x.apply_offline_patches()
x.create_pacman_conf_for_sync(archinstall.arguments.get('pacman-conf', 'copy'))
x.load_default_packages()

# Move back the saved caches
if archinstall.arguments.get('save-offline-repository-cache', False):
	x.move_folder(x._pacman_package_cache_dir, pathlib.Path(f"./{x._pacman_package_cache_dir.name}"), force=True)
	x.move_folder(x._pacman_temporary_database, pathlib.Path(f"./{x._pacman_temporary_database.name}"), force=True)

if archinstall.arguments.get('save-builddir-package-cache', False):
	pass

if archinstall.arguments.get('autorun-archinstall', False):
	with open(f'{x._build_dir}/airootfs/root/.zprofile', 'w') as zprofile:
		zprofile.write('[[ -z $DISPLAY && $XDG_VTNR -eq 1 ]] && sh -c "archinstall --offline"')

x.build_aur_packages()
x.download_package_list()
x.update_offline_repo_database()
x.write_packages_to_package_file()
x.create_pacman_conf_for_build()

if archinstall.arguments.get('breakpoint', None):
	input(f'Breakpoint before mkarchiso! Do final changes to {x._build_dir}')

archinstall.log(f"==> Creating ISO (this will take time)", fg="teal", level=logging.INFO)
if (iso := archinstall.SysCommand(f"/bin/bash -c \"mkarchiso -C {x._pacman_build_conf} -v -w {x._build_dir}/work/ -o {x._build_dir}/out/ {x._build_dir}\"", working_directory=x._build_dir, peak_output=archinstall.arguments.get('verbose', False))).exit_code != 0:
	archinstall.log(iso, level=logging.ERROR, fg="red")
	archinstall.log(iso.exit_code)
	exit(1)

archinstall.log(f"==> Your ISO has been created in {x._build_dir}/out/", fg="green", level=logging.INFO)