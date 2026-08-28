// Light/dark theme toggle. Dark is the default.
function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

function applyThemeLabel() {
    var theme = currentTheme();
    document.querySelectorAll('.theme-toggle').forEach(function (btn) {
        var icon = btn.querySelector('.theme-icon');
        var text = btn.querySelector('.theme-text');
        // Show the action the button will perform.
        if (theme === 'dark') {
            if (icon) icon.textContent = '\u2600';   // sun
            if (text) text.textContent = 'Light';
        } else {
            if (icon) icon.textContent = '\u263D';   // moon
            if (text) text.textContent = 'Dark';
        }
    });
}

function toggleTheme() {
    var next = currentTheme() === 'dark' ? 'light' : 'dark';
    if (next === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
    localStorage.setItem('theme', next);
    applyThemeLabel();
}

document.addEventListener('DOMContentLoaded', applyThemeLabel);
