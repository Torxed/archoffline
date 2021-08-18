#!/usr/bin/python

from archinstall import archinstall
import pathlib
import shutil
import glob
import logging
import re
import os
import urllib.request

print(f"Using archinstall {archinstall}")

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
	  Uses the current HTTPS enabled mirrors from this region.

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
	  Clones in archinstall master branch and adds it to autostart.
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

Examples:

	sudo python offline.py --mirrors=Sweden --packages="nano wget" --rebuild
""")
	exit(0)

if os.getuid() != 0:
	raise PermissionError("This tool requires root permission.")

REPO_NAME=archinstall.arguments.get('repo', 'localrepo')
BUILD_DIR=pathlib.Path(archinstall.arguments.get('builddir', './archiso_offline/')).absolute()
pacman_temporary_database = pathlib.Path(f'{BUILD_DIR}/tmp.pacdb/').absolute()
pacman_package_cache_dir = pathlib.Path(f'{BUILD_DIR}/airootfs/root/{REPO_NAME}/').absolute()
pacman_build_config = f'{BUILD_DIR}/pacman.build.conf'

def copy_archiso_config_directory(dst, conf):
	archinstall.log(f"Copying the Arch ISO configuration {conf} to new build door {dst}.", level=logging.INFO)
	for obj in glob.glob(f'/usr/share/archiso/configs/{conf}/*'):
		if os.path.isdir(obj):
			shutil.copytree(obj, f"{dst}/{obj.split('/')[-1]}", symlinks=True)
		else:
			shutil.copy2(obj, f"{dst}/{obj.split('/')[-1]}")

def get_default_packages(builddir):
	with open(f"{builddir}/packages.x86_64", 'r') as packages:
		for line in packages:
			if line[0] == '#': continue
			if len(line.strip()) == 0: continue
			yield line.strip()

def setup_builddir(main, pacdb, cachedir):
	archinstall.log(f"Setting up a new build directory in {main}", level=logging.INFO)
	if main.exists():
		shutil.rmtree(f"{main}")

	main.mkdir(parents=True, exist_ok=True)
	copy_archiso_config_directory(main, archinstall.arguments.get('template', 'releng'))

	modify_archiso_config_directory(main)

	pacdb.mkdir(parents=True, exist_ok=True)
	if not archinstall.arguments.get('save-cache', None):
		# This will be taken care of by --save-cache otherwise.
		cachedir.mkdir(parents=True, exist_ok=True)

def modify_archiso_config_directory(main):
	reflector_config = main/"airootfs"/"etc"/"systemd"/"system"/"reflector.service.d"/"archiso.conf"
	if reflector_config.exists():
		archinstall.log(f"Removed reflector service from ISO", level=logging.INFO, fg="yellow")
		reflector_config.unlink()

def get_mirrors():
	if not (mirror_region_data := archinstall.arguments.get('mirrors', None)):
		mirror_region_data = archinstall.select_mirror_regions(archinstall.list_mirrors())
		if not mirror_region_data:
			raise archinstall.RequirementError("A mirror region is required. Future versions will source /etc/pacman.d/mirrors.")

		mirrors = list(list(mirror_region_data.values())[0].keys())
	else:
		mirror_region_data = archinstall.list_mirrors()[mirror_region_data]
		mirrors = list(mirror_region_data.keys())
	return mirrors

def download_file(url, destination):
	urllib.request.urlretrieve(url, destination)

def untar_file(file):
	archinstall.SysCommand(f"/usr/bin/sudo -H -u {archinstall.arguments.get('aur-user', 'aoffline_usr')} /usr/bin/tar --directory /home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/ -xvzf {file}")
		
if archinstall.arguments.get('rebuild', None) or BUILD_DIR.exists() is False:
	save_cache = False
	cache_folder_name = pathlib.Path(pacman_package_cache_dir).name
	if archinstall.arguments.get('save-cache', False):
		if pathlib.Path(pacman_package_cache_dir).exists():
			archinstall.log(f"Moved {cache_folder_name} temporarily to {pathlib.Path('./').absolute()}", level=logging.INFO)
			shutil.move(pacman_package_cache_dir, './')
			save_cache = True

	setup_builddir(BUILD_DIR, pacman_temporary_database, pacman_package_cache_dir)

	if save_cache:
		archinstall.log(f"Moved back cache directory: '{cache_folder_name}' to '{pacman_package_cache_dir.parent}'", level=logging.INFO)
		shutil.move('./'+cache_folder_name, str(pacman_package_cache_dir.parent))

archinstall.log(f"Getting mirror list from the given region.", level=logging.INFO)
mirrors = get_mirrors()

archinstall.log(f"Patching pacman build configuration file.", level=logging.INFO)
with open(pacman_build_config, 'w') as pac_conf:
	mirror_str_list = '\n'.join(f"Server = {mirror}" for mirror in mirrors)

	pac_conf.write(f"[options]\n")
	pac_conf.write(f"DBPath      = {pacman_temporary_database}\n")
	pac_conf.write(f"CacheDir    = {pacman_package_cache_dir}\n")
	pac_conf.write(f"HoldPkg     = pacman glibc\n")
	pac_conf.write(f"Architecture = auto\n")
	pac_conf.write(f"\n")
	pac_conf.write(f"CheckSpace\n")
	pac_conf.write(f"\n")
	pac_conf.write(f"SigLevel    = Required DatabaseOptional\n")
	pac_conf.write(f"LocalFileSigLevel = Optional\n")
	pac_conf.write(f"\n")
	pac_conf.write(f"[core]\n")
	pac_conf.write(f"{mirror_str_list}\n")
	pac_conf.write(f"[extra]\n")
	pac_conf.write(f"{mirror_str_list}\n")
	pac_conf.write(f"[community]\n")
	pac_conf.write(f"{mirror_str_list}\n")

if not (packages := archinstall.arguments.get('packages', None)):
	packages = input('Enter any additional packages to include aside from packages.x86_64 (space separated): ').strip() or []

if packages:
	packages = packages.split(' ')

if not (aur_packages := archinstall.arguments.get('aur-packages', None)):
	aur_packages = input('Enter any additional AUR packages to include aside from aur_packages.x86_64 (space separated): ').strip() or []

if aur_packages:
	aur_packages = aur_packages.split(' ')

archinstall.log(f"Validating additional packages...", level=logging.INFO)
try:
	archinstall.validate_package_list(packages)
except archinstall.RequirementError as e:
	archinstall.log(e, fg='red')
	exit(1)

#archinstall.log(f"Validating additional packages...", level=logging.INFO)
#try:
#	archinstall.validate_aur_package_list(aur_packages)
#except archinstall.RequirementError as e:
#	archinstall.log(e, fg='red')
#	exit(1)

packages = packages + list(get_default_packages(BUILD_DIR))

if archinstall.arguments.get('verbose', None):
	archinstall.log(f"Syncronizing packages: {packages}")
else:
	archinstall.log(f"Syncronizing {len(packages)} packages (this might take a while)")

if (pacman := archinstall.SysCommand(f"pacman --noconfirm --config {pacman_build_config} -Syw {' '.join(packages)}")).exit_code != 0:
	print(pacman.exit_code)
	print(b''.join(pacman))
	exit(1)

if archinstall.arguments.get('verbose', None):
	archinstall.log(f"Syncronizing AUR packages: {aur_packages}")
else:
	archinstall.log(f"Syncronizing {len(aur_packages)} AUR packages (this might take a while)")

found_aur_user = archinstall.SysCommand(f"id {archinstall.arguments.get('aur-user', 'aoffline_usr')}").exit_code == 0
found_aur_user_sudo_entry = False
sudo_entries = []
with open('/etc/sudoers', 'r') as fh:
	for line in fh:
		sudo_entries.append(line)
		if archinstall.arguments.get('aur-user', 'aoffline_usr') in line and not line.startswith('#'):
			found_aur_user_sudo_entry = True

if not found_aur_user:
	archinstall.log(f"Creating temporary build user {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
	archinstall.SysCommand(f"/usr/bin/useradd -m -N -s /bin/bash {archinstall.arguments.get('aur-user', 'aoffline_usr')}")

if not found_aur_user_sudo_entry:
	archinstall.log(f"Creating temporary sudoers entry for user {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
	with open('/etc/sudoers', 'a') as fh:
		fh.write(f"\n{archinstall.arguments.get('aur-user', 'aoffline_usr')} ALL=(ALL) NOPASSWD: ALL\n")

for package in aur_packages:
	archinstall.log(f"Building AUR package {package}", level=logging.INFO, fg="yellow")
	download_file(f"https://aur.archlinux.org/cgit/aur.git/snapshot/{package}.tar.gz", f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}.tar.gz")
	archinstall.SysCommand(f"/usr/bin/chown {archinstall.arguments.get('aur-user', 'aoffline_usr')} /home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}.tar.gz")
	untar_file(f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}.tar.gz")
	with open(f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}/PKGBUILD", 'r') as fh:
		PKGBUILD = fh.read()

	# This regexp needs to accomodate multiple keys, as well as the logic below
	gpgkeys = re.findall('validpgpkeys=\(.*\)', PKGBUILD)
	if gpgkeys:
		for key in gpgkeys:
			key = key[13:].strip('(\')"')
			archinstall.log(f"Adding GPG-key {key} to session for {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
			archinstall.SysCommand(f"/usr/bin/sudo -H -u {archinstall.arguments.get('aur-user', 'aoffline_usr')} /usr/bin/gpg --recv-keys {key}")

	archinstall.SysCommand(f"/usr/bin/sudo -H -u {archinstall.arguments.get('aur-user', 'aoffline_usr')} /bin/bash -c \"cd /home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}; makepkg --clean --force --cleanbuild --noconfirm --needed -s\"")
	shutil.move(glob.glob(f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}/*.tar.zst")[0], pacman_package_cache_dir)
	archinstall.SysCommand(f"/usr/bin/chown root. {glob.glob(str(pacman_package_cache_dir)+'/'+package+'*.tar.zst')[0]}")
	shutil.rmtree(f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}")
	pathlib.Path(f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}/{package}.tar.gz").unlink()

if not found_aur_user:
	archinstall.log(f"Removing temporary build user {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
	# Stop dirmngr and gpg-agent before removing home directory and running userdel
	archinstall.SysCommand(f"/usr/bin/systemctl --machine={archinstall.arguments.get('aur-user', 'aoffline_usr')}@.host --user stop dirmngr.socket") # Doesn't do anything?
	archinstall.SysCommand(f"/usr/bin/killall -u {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
	archinstall.SysCommand(f"/usr/bin/sudo -H -u {archinstall.arguments.get('aur-user', 'aoffline_usr')} /usr/bin/gpgconf --kill gpg-agent")
	archinstall.SysCommand(f"/usr/bin/userdel {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
	shutil.rmtree(f"/home/{archinstall.arguments.get('aur-user', 'aoffline_usr')}")

if not found_aur_user_sudo_entry:
	archinstall.log(f"Removing temporary sudoers entry for user {archinstall.arguments.get('aur-user', 'aoffline_usr')}")
	with open('/etc/sudoers', 'w') as fh:
		for line in sudo_entries:
			fh.write(line)

archinstall.log(f"Packages have been synced to {pacman_package_cache_dir}, creating repository database.")
if (repoadd := archinstall.SysCommand(f"/bin/bash -c \"repo-add {pacman_package_cache_dir}/{REPO_NAME}.db.tar.gz {pacman_package_cache_dir}/{{*.pkg.tar.xz,*.pkg.tar.zst}}\"")).exit_code != 0:
	print(repoadd.exit_code)
	print(b''.join(repoadd))
	exit(1)

archinstall.log(f"Patching ISO pacman.conf to only use the local repository.")
with open(f'{BUILD_DIR}/pacman.conf', 'r') as pac_conf:
	old_conf = pac_conf.read()

block = None
with open(f'{BUILD_DIR}/pacman.conf', 'w') as pac_conf:
	for line in old_conf.split('\n'):
		if line.lower().strip() in ('[core]', '[community]', '[extra]', f'[{REPO_NAME}]'):
			block = line.lower().strip()
			continue
		elif block is None or block not in ('[core]', '[community]', '[extra]', f'[{REPO_NAME}]'):
			pac_conf.write(f"{line.strip()}\n")
	
	pac_conf.write(f"[{REPO_NAME}]\n")
	pac_conf.write(f"SigLevel = Optional TrustAll\n")
	pac_conf.write(f"Server = file:///root/{REPO_NAME}/\n")

shutil.copy2(f'{BUILD_DIR}/pacman.conf', f'{BUILD_DIR}/airootfs/etc/pacman.conf')

if (profiles := archinstall.arguments.get('profiles', None)):
	profiles = [x for x in profiles.split(',') if len(x)]
	if not archinstall.arguments.get('archinstall', None):
		archinstall.arguments['archinstall'] = True

if archinstall.arguments.get('archinstall', None):
	archinstall.log(f"Cloning in archinstall to ISO root.")
	if (git := archinstall.SysCommand(f"/bin/bash -c \"cd {BUILD_DIR}/airootfs/root/; git clone -b {archinstall.arguments.get('ai-branch', 'master')} https://github.com/archlinux/archinstall.git archinstall-git\"", working_directory=f'{BUILD_DIR}/airootfs/root/')).exit_code != 0:
		print(git.exit_code)
		print(b''.join(git))
		exit(1)

	with open(f'{BUILD_DIR}/airootfs/root/.zprofile', 'w') as zprofile:
		zprofile.write('[[ -z $DISPLAY && $XDG_VTNR -eq 1 ]] && sh -c "cd /root/archinstall-git; cp examples/guided.py ./; python guided.py"')

if profiles:
	archinstall.log(f"Adding in additional archinstall profiles:", profiles)
	for profile in profiles:
		if pathlib.Path(profile).exists() is False:
			archinstall.log(f"Adding in additional archinstall profiles: {profiles}", fg="red", level=logging.Error)
			continue

		archinstall.log(f"Copying profile '{profile}' over to the ISO's archinstall library.")
		shutil.copy2(profile, f'{BUILD_DIR}/airootfs/root/archinstall-git/profiles/')

if archinstall.arguments.get('breakpoint', None):
	input('Breakpoint: mkarchiso')

archinstall.log(f"Creating ISO (this will take time)")
if (iso := archinstall.SysCommand(f"/bin/bash -c \"mkarchiso -C {pacman_build_config} -v -w {BUILD_DIR}/work/ -o {BUILD_DIR}/out/ {BUILD_DIR}\"", working_directory=BUILD_DIR)).exit_code != 0:
	print(iso.exit_code)
	print(b''.join(iso))
	exit(1)

iso_out = str(BUILD_DIR/"out")+"/*.iso"
print(f"ISO has been created at: {glob.glob(iso_out)}")

if archinstall.arguments.get('boot', None):
	ISO = glob.glob(iso_out)[0]
	if pathlib.Path(f"{BUILD_DIR}/test.qcow2").exists() is False:
		archinstall.SysCommand(f"qemu-img create -f qcow2 {BUILD_DIR}/test.qcow2 15G")

	archinstall.SysCommand(f"sudo qemu-system-x86_64 "
								+ "-cpu host "
								+ "-enable-kvm "
								+ "-machine q35,accel=kvm "
								+ "-device intel-iommu "
								+ "-m 2048 "
								+ "-nic none "
								+ "-drive if=pflash,format=raw,readonly,file=/usr/share/ovmf/x64/OVMF_CODE.fd  "
								+ "-drive if=pflash,format=raw,readonly,file=/usr/share/ovmf/x64/OVMF_VARS.fd "
								+ "-device virtio-scsi-pci,bus=pcie.0,id=scsi0 "
								+ "    -device scsi-hd,drive=hdd0,bus=scsi0.0,id=scsi0.0,bootindex=2 "
								+ f"        -drive file={BUILD_DIR}/test.qcow2,if=none,format=qcow2,discard=unmap,aio=native,cache=none,id=hdd0 "
								+ "-device virtio-scsi-pci,bus=pcie.0,id=scsi1 "
								+ "    -device scsi-cd,drive=cdrom0,bus=scsi1.0,bootindex=1 "
								+ f"        -drive file={ISO},media=cdrom,if=none,format=raw,cache=none,id=cdrom0")

# sudo qemu-system-x86_64         -cpu host         -enable-kvm         -machine q35,accel=kvm         -device intel-iommu         -m 8192         -drive if=pflash,format=raw,readonly,file=/usr/share/ovmf/x64/OVMF_CODE.fd          -drive if=pflash,format=raw,readonly,file=/usr/share/ovmf/x64/OVMF_VARS.fd         -device virtio-scsi-pci,bus=pcie.0,id=scsi0             -device scsi-hd,drive=hdd0,bus=scsi0.0,id=scsi0.0,bootindex=1                 -drive file=./archiso_offline/test.qcow2,if=none,format=qcow2,discard=unmap,aio=native,cache=none,id=hdd0         -device virtio-scsi-pci,bus=pcie.0,id=scsi1             -device scsi-cd,drive=cdrom0,bus=scsi1.0,bootindex=2                 -drive file=archiso_offline/out/archlinux-2021.04.12-x86_64.iso,media=cdrom,if=none,format=raw,cache=none,id=cdrom0 -nic none
