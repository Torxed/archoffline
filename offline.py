#!/usr/bin/python

import archinstall
import pathlib
import shutil
import glob
import os

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

	--profiles=[a commaseparated list of profile paths]
	  If a profile is given, it is copied into archinstall master directory.
	  This option implies --archinstall is given.

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
	if main.exists():
		shutil.rmtree(f"{main}")

	main.mkdir(parents=True, exist_ok=True)
	copy_archiso_config_directory(main, archinstall.arguments.get('template', 'releng'))

	pacdb.mkdir(parents=True, exist_ok=True)
	cachedir.mkdir(parents=True, exist_ok=True)

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

if archinstall.arguments.get('rebuild', None) or BUILD_DIR.exists() is False:
	setup_builddir(BUILD_DIR, pacman_temporary_database, pacman_package_cache_dir)

mirrors = get_mirrors()

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
try:
	archinstall.validate_package_list(packages)
except archinstall.RequirementError as e:
	archinstall.log(e, fg='red')
	exit(1)

packages = packages + list(get_default_packages(BUILD_DIR))

archinstall.log(f"Syncronizing {len(packages)} packages (this might take a while)")
if (pacman := archinstall.sys_command(f"pacman --noconfirm --config {pacman_build_config} -Syw {' '.join(packages)}")).exit_code != 0:
	print(pacman.exit_code)
	print(b''.join(pacman))
	exit(1)

archinstall.log(f"Packages have been synced to {pacman_package_cache_dir}, creating repository database.")
if (repoadd := archinstall.sys_command(f"/bin/bash -c \"repo-add {pacman_package_cache_dir}/{REPO_NAME}.db.tar.gz {pacman_package_cache_dir}/{{*.pkg.tar.xz,*.pkg.tar.zst}}\"")).exit_code != 0:
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

if (profiles := archinstall.arguments.get('profiles', '').split(',')):
	if not archinstall.arguments.get('archinstall', None):
		archinstall.arguments['archinstall'] = True

if archinstall.arguments.get('archinstall', None):
	archinstall.log(f"Cloning in archinstall to ISO root.")
	if (git := archinstall.sys_command(f"/bin/bash -c \"git clone https://github.com/archlinux/archinstall.git archinstall-git\"", workdir=f'{BUILD_DIR}/airootfs/root/')).exit_code != 0:
		print(git.exit_code)
		print(b''.join(git))
		exit(1)

	with open(f'{BUILD_DIR}/airootfs/root/.zprofile', 'w') as zprofile:
		zprofile.write('[[ -z $DISPLAY && $XDG_VTNR -eq 1 ]] && sh -c "cd /root/archinstall-git; cp examples/guided.py ./; python guided.py"')

if len(profiles):
	archinstall.log(f"Adding in additional archinstall profiles:", profiles)
	for profile in profiles:
		if pathlib.Path(profile).exists() is False:
			archinstall.log(f"Adding in additional archinstall profiles: {profiles}", fg="red", level=archinstall.LOG_LEVELS.Error)
			continue

		archinstall.log(f"Copying profile '{profile}' over to the ISO's archinstall library.")
		shutil.copy2(profile, f'{BUILD_DIR}/airootfs/root/archinstall-git/profiles/')

if archinstall.arguments.get('breakpoint', None):
	input('Breakpoint: mkarchiso')

archinstall.log(f"Creating ISO (this will take time)")
if (iso := archinstall.sys_command(f"/bin/bash -c \"mkarchiso -C {pacman_build_config} -v -w work/ -o out/ ./\"", workdir=BUILD_DIR)).exit_code != 0:
	print(iso.exit_code)
	print(b''.join(iso))
	exit(1)

iso_out = str(BUILD_DIR/"out")+"/*.iso"
print(f"ISO has been created at: {glob.glob(iso_out)}")

if archinstall.arguments.get('boot', None):
	ISO = glob.glob(iso_out)[0]
	if pathlib.Path(f"{BUILD_DIR}/test.qcow2").exists() is False:
		archinstall.sys_command(f"qemu-img create -f qcow2 {BUILD_DIR}/test.qcow2 15G")

	archinstall.sys_command(f"sudo qemu-system-x86_64 "
								+ "-cpu host "
								+ "-enable-kvm "
								+ "-machine q35,accel=kvm "
								+ "-device intel-iommu "
								+ "-m 2048 "
								+ "-nic none"
								+ "-drive if=pflash,format=raw,readonly,file=/usr/share/ovmf/x64/OVMF_CODE.fd  "
								+ "-drive if=pflash,format=raw,readonly,file=/usr/share/ovmf/x64/OVMF_VARS.fd "
								+ "-device virtio-scsi-pci,bus=pcie.0,id=scsi0 "
								+ "    -device scsi-hd,drive=hdd0,bus=scsi0.0,id=scsi0.0,bootindex=2 "
								+ f"        -drive file={BUILD_DIR}/test.qcow2,if=none,format=qcow2,discard=unmap,aio=native,cache=none,id=hdd0 "
								+ "-device virtio-scsi-pci,bus=pcie.0,id=scsi1 "
								+ "    -device scsi-cd,drive=cdrom0,bus=scsi1.0,bootindex=1 "
								+ f"        -drive file={ISO},media=cdrom,if=none,format=raw,cache=none,id=cdrom0")