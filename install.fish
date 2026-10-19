#!/usr/bin/env fish
# Install the banners into your fish config.
#
#   ./install.fish          symlink (so `git pull` here updates them)
#   ./install.fish --copy   copy instead
#
# Anything already in the way is moved aside to <name>.bak.<timestamp>.

argparse copy h/help -- $argv; or exit 1
if set -q _flag_help
	sed -n '2,7p' (status filename) | string replace -r '^# ?' ''
	exit
end

set -l repo (path resolve (status dirname))
set -l config $__fish_config_dir

for item in banners functions/fish_greeting.fish
	set -l source $repo/$item
	set -l target $config/$item
	if test -L $target; and test (path resolve $target) = $source
		echo "already linked: $target"
		continue
	end
	if test -e $target -o -L $target
		set -l backup $target.bak.(date +%s)
		mv $target $backup
		echo "moved aside:    $target → $backup"
	end
	mkdir -p (path dirname $target)
	if set -q _flag_copy
		cp -R $source $target
		echo "copied:         $target"
	else
		ln -s $source $target
		echo "linked:         $target → $source"
	end
end

if not command -q uv
	echo
	echo "Note: the image banners run through uv, which isn't installed:"
	echo "  brew install uv    (or see https://docs.astral.sh/uv/)"
end
if not test -e ~/.hushlogin
	echo
	echo "Tip: `touch ~/.hushlogin` hides macOS's \"Last login\" line above the banner."
end
echo
echo "Open a new Ghostty or kitty window to see a banner."
