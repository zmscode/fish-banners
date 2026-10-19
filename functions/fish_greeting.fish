function fish_greeting --description 'Random animated banner (see banners/), cleared on the first Enter'
	# Ghostty and kitty can show a real image; anything else gets braille art.
	if not __banner_supported; or not __banner_show
		python3 $__fish_config_dir/banners/_text.py 12
		return
	end
	__banner_arm
end

function __banner_supported
	test "$TERM_PROGRAM" = ghostty -o "$TERM" = xterm-kitty; and not set -q TMUX
end

# Show a random banner and remember its animator and
# image so the first Enter can clean up.
function __banner_show
	set -l state (mktemp -t banner)
	if not uv run --quiet --script $__fish_config_dir/banners/run.py 10 $state
		rm -f $state
		return 1
	end
	# "<animator pid> <image id>"
	set -l s (string split ' ' -- (cat $state))
	rm -f $state
	set -ga __banner_pids $s[1]
	set -ga __banner_images $s[2]
end

# Take over Enter once: stop the animations, drop the images, clear the
# screen, then hand the keypress to whatever Enter did before.
function __banner_arm
	set -q __banner_enter; and return                 # already armed
	set -g __banner_enter (bind enter 2>/dev/null)
	bind enter __banner_dismiss
end

function __banner_dismiss
	bind --erase enter
	for line in $__banner_enter
		eval $line
	end

	kill $__banner_pids 2>/dev/null
	for id in $__banner_images
		printf '\e_Ga=d,d=I,i=%s,q=2\e\\' $id
	end
	set -e __banner_pids __banner_images __banner_enter

	commandline -f clear-screen
	# Replay Enter through its restored binding (fish's execute by default).
	commandline -f (bind enter 2>/dev/null | string match -rg '^bind (?:-M \S+ )?enter (.+)$'; or echo execute)
end
