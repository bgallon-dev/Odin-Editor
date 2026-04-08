package editor

import rl "vendor:raylib"
import "core:os"
import "core:fmt"
import "core:strings"
import "core:strconv"

// ---------------------------------------------------------------------------
// Runtime configuration — replaces compile-time :: constants.
// Loaded from "editor.conf" at startup; falls back to built-in defaults.
// ---------------------------------------------------------------------------

Config :: struct {
    // Window
    window_w:           int,
    window_h:           int,

    // Font / text
    font_size:          int,
    line_height:        int,

    // Layout
    gutter_w:           int,
    top_margin:         int,
    search_bar_h:       int,
    tab_bar_h:          int,
    tab_w:              int,

    // Timing
    cursor_blink:       f64,
    double_click_t:     f64,

    // Minimap
    minimap_w:          int,
    minimap_char_w:     f32,
    minimap_line_h:     int,

    // Colors — editor core
    bg_color:           rl.Color,
    text_color:         rl.Color,
    cursor_color:       rl.Color,
    linenum_color:      rl.Color,
    sel_color:          rl.Color,
    status_bg:          rl.Color,
    status_fg:          rl.Color,
    curline_color:      rl.Color,
    status_warn_bg:     rl.Color,
    search_bg:          rl.Color,
    search_border:      rl.Color,
    search_match_clr:   rl.Color,
    search_cur_match:   rl.Color,
    tab_bg:             rl.Color,
    tab_active_bg:      rl.Color,
    tab_border:         rl.Color,
    tab_text:           rl.Color,
    tab_active_text:    rl.Color,
    bracket_hl:         rl.Color,
    minimap_bg:         rl.Color,
    minimap_view:       rl.Color,
    minimap_text:       rl.Color,

    // Side panel
    side_panel_w_default: int,
    side_panel_w_min:     int,
    side_panel_w_max:     int,
    side_panel_drag_w:    int,
    side_panel_bg:        rl.Color,
    side_panel_border:    rl.Color,
    side_panel_header:    rl.Color,
    side_panel_text:      rl.Color,
    side_panel_tab_h:     int,
    side_panel_btn_h:     int,
    side_panel_btn_bg:    rl.Color,
    side_panel_btn_hov:   rl.Color,
    side_panel_line_h:    int,

    // Severity colors
    severity_info_clr:    rl.Color,
    severity_warning_clr: rl.Color,
    severity_error_clr:   rl.Color,

    // File browser / sidebar
    sidebar_w:          int,
    sidebar_bg:         rl.Color,
    sidebar_hover:      rl.Color,
    sidebar_text:       rl.Color,
    sidebar_dir_clr:    rl.Color,
    sidebar_border:     rl.Color,
    fb_line_h:          int,
    fb_indent:          int,

    // Quick open
    quickopen_max_results: int,
}

// Package-level global — available everywhere in `package editor`.
cfg: Config

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------
config_defaults :: proc() -> Config {
    return Config{
        // Window
        window_w           = 1100,
        window_h           = 700,
        // Font
        font_size          = 20,
        line_height        = 24,
        // Layout
        gutter_w           = 50,
        top_margin         = 10,
        search_bar_h       = 28,
        tab_bar_h          = 28,
        tab_w              = 140,
        // Timing
        cursor_blink       = 0.5,
        double_click_t     = 0.35,
        // Minimap
        minimap_w          = 60,
        minimap_char_w     = 1.5,
        minimap_line_h     = 3,
        // Colors — editor core
        bg_color           = {30,  30,  30,  255},
        text_color         = {212, 212, 212, 255},
        cursor_color       = {220, 220, 170, 255},
        linenum_color      = {100, 100, 100, 255},
        sel_color          = {60,  90,  150, 120},
        status_bg          = {50,  50,  50,  255},
        status_fg          = {180, 180, 180, 255},
        curline_color      = {40,  40,  40,  255},
        status_warn_bg     = {120, 60,  30,  255},
        search_bg          = {45,  45,  45,  255},
        search_border      = {80,  80,  80,  255},
        search_match_clr   = {180, 140, 50,  80},
        search_cur_match   = {220, 170, 60,  120},
        tab_bg             = {40,  40,  40,  255},
        tab_active_bg      = {30,  30,  30,  255},
        tab_border         = {60,  60,  60,  255},
        tab_text           = {160, 160, 160, 255},
        tab_active_text    = {220, 220, 220, 255},
        bracket_hl         = {255, 255, 100, 60},
        minimap_bg         = {25,  25,  25,  255},
        minimap_view       = {80,  80,  80,  40},
        minimap_text       = {120, 120, 120, 180},
        // Side panel
        side_panel_w_default = 300,
        side_panel_w_min     = 150,
        side_panel_w_max     = 600,
        side_panel_drag_w    = 5,
        side_panel_bg        = {28,  28,  28,  255},
        side_panel_border    = {50,  50,  50,  255},
        side_panel_header    = {35,  35,  35,  255},
        side_panel_text      = {180, 180, 180, 255},
        side_panel_tab_h     = 28,
        side_panel_btn_h     = 26,
        side_panel_btn_bg    = {55,  55,  55,  255},
        side_panel_btn_hov   = {70,  70,  70,  255},
        side_panel_line_h    = 22,
        severity_info_clr    = {97,  175, 239, 255},
        severity_warning_clr = {229, 192, 123, 255},
        severity_error_clr   = {224, 108, 117, 255},
        // File browser
        sidebar_w          = 220,
        sidebar_bg         = {25,  25,  25,  255},
        sidebar_hover      = {45,  45,  45,  255},
        sidebar_text       = {180, 180, 180, 255},
        sidebar_dir_clr    = {140, 170, 220, 255},
        sidebar_border     = {50,  50,  50,  255},
        fb_line_h          = 22,
        fb_indent          = 16,
        // Quick open
        quickopen_max_results = 15,
    }
}

// ---------------------------------------------------------------------------
// Load from file — applies values on top of defaults
// ---------------------------------------------------------------------------
config_load :: proc(path: string) -> Config {
    c := config_defaults()
    data, ok := os.read_entire_file(path)
    if !ok {
        fmt.printfln("[CONFIG] no config file at '%s', using defaults", path)
        return c
    }
    defer delete(data)

    text := string(data)
    for line in strings.split_lines_iterator(&text) {
        l := strings.trim_space(line)
        if len(l) == 0 || l[0] == '#' do continue

        eq := strings.index_byte(l, '=')
        if eq < 0 do continue

        key := strings.trim_space(l[:eq])
        val := strings.trim_space(l[eq+1:])

        // --- Window ---
        if      key == "window_w"           do c.window_w           = parse_int(val, c.window_w)
        else if key == "window_h"           do c.window_h           = parse_int(val, c.window_h)
        // --- Font ---
        else if key == "font_size"          do c.font_size          = parse_int(val, c.font_size)
        else if key == "line_height"        do c.line_height        = parse_int(val, c.line_height)
        // --- Layout ---
        else if key == "gutter_w"           do c.gutter_w           = parse_int(val, c.gutter_w)
        else if key == "top_margin"         do c.top_margin         = parse_int(val, c.top_margin)
        else if key == "search_bar_h"       do c.search_bar_h       = parse_int(val, c.search_bar_h)
        else if key == "tab_bar_h"          do c.tab_bar_h          = parse_int(val, c.tab_bar_h)
        else if key == "tab_w"              do c.tab_w              = parse_int(val, c.tab_w)
        // --- Timing ---
        else if key == "cursor_blink"       do c.cursor_blink       = parse_f64(val, c.cursor_blink)
        else if key == "double_click_t"     do c.double_click_t     = parse_f64(val, c.double_click_t)
        // --- Minimap ---
        else if key == "minimap_w"          do c.minimap_w          = parse_int(val, c.minimap_w)
        else if key == "minimap_char_w"     do c.minimap_char_w     = parse_f32(val, c.minimap_char_w)
        else if key == "minimap_line_h"     do c.minimap_line_h     = parse_int(val, c.minimap_line_h)
        // --- Colors ---
        else if key == "bg_color"           do c.bg_color           = parse_color(val, c.bg_color)
        else if key == "text_color"         do c.text_color         = parse_color(val, c.text_color)
        else if key == "cursor_color"       do c.cursor_color       = parse_color(val, c.cursor_color)
        else if key == "linenum_color"      do c.linenum_color      = parse_color(val, c.linenum_color)
        else if key == "sel_color"          do c.sel_color          = parse_color(val, c.sel_color)
        else if key == "status_bg"          do c.status_bg          = parse_color(val, c.status_bg)
        else if key == "status_fg"          do c.status_fg          = parse_color(val, c.status_fg)
        else if key == "curline_color"      do c.curline_color      = parse_color(val, c.curline_color)
        else if key == "status_warn_bg"     do c.status_warn_bg     = parse_color(val, c.status_warn_bg)
        else if key == "search_bg"          do c.search_bg          = parse_color(val, c.search_bg)
        else if key == "search_border"      do c.search_border      = parse_color(val, c.search_border)
        else if key == "search_match_clr"   do c.search_match_clr   = parse_color(val, c.search_match_clr)
        else if key == "search_cur_match"   do c.search_cur_match   = parse_color(val, c.search_cur_match)
        else if key == "tab_bg"             do c.tab_bg             = parse_color(val, c.tab_bg)
        else if key == "tab_active_bg"      do c.tab_active_bg      = parse_color(val, c.tab_active_bg)
        else if key == "tab_border"         do c.tab_border         = parse_color(val, c.tab_border)
        else if key == "tab_text"           do c.tab_text           = parse_color(val, c.tab_text)
        else if key == "tab_active_text"    do c.tab_active_text    = parse_color(val, c.tab_active_text)
        else if key == "bracket_hl"         do c.bracket_hl         = parse_color(val, c.bracket_hl)
        else if key == "minimap_bg"         do c.minimap_bg         = parse_color(val, c.minimap_bg)
        else if key == "minimap_view"       do c.minimap_view       = parse_color(val, c.minimap_view)
        else if key == "minimap_text"       do c.minimap_text       = parse_color(val, c.minimap_text)
        // --- Side panel ---
        else if key == "side_panel_w_default" do c.side_panel_w_default = parse_int(val, c.side_panel_w_default)
        else if key == "side_panel_w_min"     do c.side_panel_w_min     = parse_int(val, c.side_panel_w_min)
        else if key == "side_panel_w_max"     do c.side_panel_w_max     = parse_int(val, c.side_panel_w_max)
        else if key == "side_panel_drag_w"    do c.side_panel_drag_w    = parse_int(val, c.side_panel_drag_w)
        else if key == "side_panel_bg"        do c.side_panel_bg        = parse_color(val, c.side_panel_bg)
        else if key == "side_panel_border"    do c.side_panel_border    = parse_color(val, c.side_panel_border)
        else if key == "side_panel_header"    do c.side_panel_header    = parse_color(val, c.side_panel_header)
        else if key == "side_panel_text"      do c.side_panel_text      = parse_color(val, c.side_panel_text)
        else if key == "side_panel_tab_h"     do c.side_panel_tab_h     = parse_int(val, c.side_panel_tab_h)
        else if key == "side_panel_btn_h"     do c.side_panel_btn_h     = parse_int(val, c.side_panel_btn_h)
        else if key == "side_panel_btn_bg"    do c.side_panel_btn_bg    = parse_color(val, c.side_panel_btn_bg)
        else if key == "side_panel_btn_hov"   do c.side_panel_btn_hov   = parse_color(val, c.side_panel_btn_hov)
        else if key == "side_panel_line_h"    do c.side_panel_line_h    = parse_int(val, c.side_panel_line_h)
        else if key == "severity_info_clr"    do c.severity_info_clr    = parse_color(val, c.severity_info_clr)
        else if key == "severity_warning_clr" do c.severity_warning_clr = parse_color(val, c.severity_warning_clr)
        else if key == "severity_error_clr"   do c.severity_error_clr   = parse_color(val, c.severity_error_clr)
        // --- File browser ---
        else if key == "sidebar_w"          do c.sidebar_w          = parse_int(val, c.sidebar_w)
        else if key == "sidebar_bg"         do c.sidebar_bg         = parse_color(val, c.sidebar_bg)
        else if key == "sidebar_hover"      do c.sidebar_hover      = parse_color(val, c.sidebar_hover)
        else if key == "sidebar_text"       do c.sidebar_text       = parse_color(val, c.sidebar_text)
        else if key == "sidebar_dir_clr"    do c.sidebar_dir_clr    = parse_color(val, c.sidebar_dir_clr)
        else if key == "sidebar_border"     do c.sidebar_border     = parse_color(val, c.sidebar_border)
        else if key == "fb_line_h"          do c.fb_line_h          = parse_int(val, c.fb_line_h)
        else if key == "fb_indent"          do c.fb_indent          = parse_int(val, c.fb_indent)
        // --- Quick open ---
        else if key == "quickopen_max_results" do c.quickopen_max_results = parse_int(val, c.quickopen_max_results)
        else {
            fmt.printfln("[CONFIG] unknown key: '%s'", key)
        }
    }

    fmt.printfln("[CONFIG] loaded from '%s'", path)
    return c
}

// ---------------------------------------------------------------------------
// Parse helpers — return fallback on failure
// ---------------------------------------------------------------------------
@(private="file")
parse_int :: proc(s: string, fallback: int) -> int {
    v, ok := strconv.parse_int(s)
    return v if ok else fallback
}

@(private="file")
parse_f64 :: proc(s: string, fallback: f64) -> f64 {
    v, ok := strconv.parse_f64(s)
    return v if ok else fallback
}

@(private="file")
parse_f32 :: proc(s: string, fallback: f32) -> f32 {
    v, ok := strconv.parse_f64(s)
    return f32(v) if ok else fallback
}

// Parse "r g b a" → rl.Color
@(private="file")
parse_color :: proc(s: string, fallback: rl.Color) -> rl.Color {
    parts: [4]u8
    idx := 0
    rest := s
    for idx < 4 {
        rest = strings.trim_left_space(rest)
        if len(rest) == 0 do return fallback

        // Find end of token
        end := 0
        for end < len(rest) && rest[end] != ' ' && rest[end] != '\t' { end += 1 }
        token := rest[:end]
        v, ok := strconv.parse_int(token)
        if !ok || v < 0 || v > 255 do return fallback
        parts[idx] = u8(v)
        idx += 1
        rest = rest[end:]
    }
    return rl.Color{parts[0], parts[1], parts[2], parts[3]}
}
