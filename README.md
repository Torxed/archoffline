This is a helper script to create Arch Linux ISO's that support
Offline installations. It requires root privileges due to archiso itself.

Usage:

	sudo python -m archoffline

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

	--skip-validation
	  Skips validation of packages and AUR packages. Improves build speed.

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

	--archinstall
	  Clones in archinstall to /root/archinstall-git with the given --ai-branch and
	  --ai-url.
	  (This is optional, archinstall stable is shipped as a package already)

	--ai-branch=<archinstall branch to clone>
	  This can override the default `master` branch.

    --ai-url=https://github.com/archlinux/archinstall.git

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

	--autorun="<commands to autorun on every boot>"
	  This injects a .zprofile auto-run string, be mindful of quotation issues:
	  Injects: [[ -z $DISPLAY && $XDG_VTNR -eq 1 ]] && sh -c "{string}"

	--save-offline-repository-cache
	  Saves the offline repository cache (packages) in the ISO between rebuilds.
	  It's done by moving out the `--repo` folder/cache out and in the --builddir.

	--silent
	  Does not prompt for anything, will skip by default or error out if key parameters
	  were not found during execution.

Examples:

	sudo python -m archoffline --mirrors=Sweden --packages="nano wget" --rebuild
