//Prevents from error request page
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.alert').forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        }, 4000);
    });
    document.querySelectorAll('.plv-modal-backdrop').forEach(backdrop => {
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) {
                backdrop.classList.remove('open');
            }
        });
    });
});

function togglePassword(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';

    if (btn) {
        btn.setAttribute('aria-label', isHidden ? 'Hide password' : 'Show password');
        btn.style.opacity = isHidden ? '1' : '0.65';
    }
}

//View pasword
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.password-toggle, .toggle-password, [data-toggle-password]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = btn.getAttribute('data-target') || btn.getAttribute('data-toggle-password');
            const input = targetId
                ? document.getElementById(targetId)
                : btn.parentElement.querySelector('input[type="password"], input[type="text"]');

            if (input) {
                input.type = input.type === 'password' ? 'text' : 'password';
            }
        });
    });
});

function togglePasswordVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';

    if (btn) {
        btn.style.opacity = isHidden ? '1' : '0.6';
    }
}