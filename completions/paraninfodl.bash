_paraninfodl() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="--quality --text-layer --keep-pages --output --jobs --no-cache --version --help"

    case "$prev" in
        --quality|--jobs) return 0 ;;
        -o|--output) COMPREPLY=( $(compgen -f -- "$cur") ); return 0 ;;
    esac

    [[ "$cur" == -* ]] && COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
}
complete -F _paraninfodl paraninfodl
