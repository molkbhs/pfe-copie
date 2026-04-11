/**
 * contact.js - Contact form with backend SMTP first, EmailJS fallback.
 */

(() => {
  const EMAILJS_PUBLIC_KEY = 'BYgRqzNshOFw_Jh93';
  const EMAILJS_SERVICE_ID = 'service_7lz02b7';
  const EMAILJS_TEMPLATE_ID = 'template_kk0s40n';

  const API_BASE = window.API_BASE || ((window.location.origin && window.location.origin !== 'null')
    ? window.location.origin
    : 'http://127.0.0.1:5000');
  const CONTACT_ENDPOINT = `${API_BASE}/api/contact`;

  const user = window.UICore?.bootstrapAuthenticatedPage?.({
    requireAuth: true,
    refreshProfile: true
  }) || null;

  if (!user) {
    window.location.replace('index.html');
    return;
  }

  const escapeHtml = (value) => {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  };

  const makeError = (code, message, meta = {}) => {
    const err = new Error(message || code);
    err.code = code;
    Object.assign(err, meta);
    return err;
  };

  const setFeedback = (message, ok = true) => {
    const box = document.getElementById('successMessage');
    if (!box) return;

    const icon = ok ? 'bi-check-circle-fill' : 'bi-x-circle-fill';
    box.innerHTML = `<i class="bi ${icon} me-1"></i>${escapeHtml(message)}`;
    box.style.background = ok ? 'rgba(72, 187, 120, 0.2)' : 'rgba(245, 101, 101, 0.2)';
    box.style.borderColor = ok ? '#48BB78' : '#f56565';
    box.classList.add('show');
    window.setTimeout(() => box.classList.remove('show'), 5000);
  };

  const normalizeNewsletter = (value) => {
    const text = String(value || '').trim().toLowerCase();
    return ['oui', 'yes', 'true', '1'].includes(text) ? 'Oui' : 'Non';
  };

  const validateFormData = (formData) => {
    if (!formData.name || !formData.email || !formData.subject || !formData.message) {
      return 'Veuillez remplir tous les champs obligatoires.';
    }

    const emailRegex = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
    if (!emailRegex.test(formData.email)) {
      return 'Veuillez saisir un email valide.';
    }

    return '';
  };

  const backendFailureText = (error) => {
    if (!error) return '';
    if (error.code === 'smtp_not_configured') {
      return 'Le service email serveur est indisponible.';
    }
    if (error.code === 'smtp_send_failed') {
      return 'Le serveur email a refuse le message.';
    }
    if (error.code === 'backend_parse_error') {
      return 'Reponse serveur invalide.';
    }
    if (error.status) {
      return `Erreur serveur (${error.status}).`;
    }
    return error.message || 'Erreur serveur inconnue.';
  };

  const emailJsFailureText = (error) => {
    if (!error) return '';
    if (error.code === 'emailjs_not_loaded') {
      return 'EmailJS n\'est pas charge dans la page.';
    }
    if (error.code === 'emailjs_failed') {
      return `EmailJS a refuse l\'envoi (${error.status || 'status inconnu'}).`;
    }
    return error.message || 'Echec EmailJS.';
  };

  const tryBackend = async (formData) => {
    const response = await fetch(CONTACT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(formData)
    });

    const payload = await response.json().catch(() => {
      throw makeError('backend_parse_error', 'Reponse serveur invalide', { status: response.status });
    });

    if (!response.ok || payload?.success === false) {
      throw makeError(
        payload?.error || `HTTP_${response.status}`,
        payload?.message || payload?.error || `Erreur backend (${response.status})`,
        { status: response.status, detail: payload?.detail || '' }
      );
    }

    return payload;
  };

  const tryEmailJs = async (emailData) => {
    if (!window.emailjs || typeof window.emailjs.send !== 'function') {
      throw makeError('emailjs_not_loaded', 'EmailJS non charge');
    }

    try {
      const result = await window.emailjs.send(EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, emailData);
      if (!result || (typeof result.status !== 'undefined' && Number(result.status) !== 200)) {
        throw makeError('emailjs_failed', 'EmailJS a retourne un statut invalide', { status: result?.status });
      }
      return result;
    } catch (error) {
      if (error?.code) throw error;
      throw makeError('emailjs_failed', error?.text || error?.message || 'EmailJS error', {
        status: error?.status,
        detail: error?.text || ''
      });
    }
  };

  const toEmailPayload = (formData) => ({
    user_name: formData.name,
    user_email: formData.email,
    user_phone: formData.phone || 'Non renseigne',
    subject: formData.subject,
    message: formData.message,
    newsletter: formData.newsletter,
    from_name: formData.name,
    from_email: formData.email,
    phone: formData.phone || 'Non renseigne',
    time: new Date().toLocaleString('fr-TN')
  });

  const initEmailJs = () => {
    if (!window.emailjs || typeof window.emailjs.init !== 'function') return;
    try {
      window.emailjs.init({ publicKey: EMAILJS_PUBLIC_KEY });
    } catch (_) {
      window.emailjs.init(EMAILJS_PUBLIC_KEY);
    }
  };

  const initFormAnimation = () => {
    if (typeof window.IntersectionObserver !== 'function') return;

    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }),
      { threshold: 0.1, rootMargin: '0px 0px -100px 0px' }
    );

    document.querySelectorAll('.form-group').forEach((el) => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(20px)';
      el.style.transition = 'all 0.6s ease-out';
      observer.observe(el);
    });
  };

  const bindFormSubmit = (form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();

      const button = form.querySelector('button[type="submit"]');
      const originalText = button?.textContent || 'Envoyer le message';

      const formData = {
        name: document.getElementById('name')?.value.trim() || '',
        email: document.getElementById('email')?.value.trim() || '',
        phone: document.getElementById('phone')?.value.trim() || '',
        subject: document.getElementById('subject')?.value || '',
        message: document.getElementById('message')?.value.trim() || '',
        newsletter: normalizeNewsletter(document.getElementById('newsletter')?.checked ? 'oui' : 'non')
      };

      const validationError = validateFormData(formData);
      if (validationError) {
        setFeedback(validationError, false);
        return;
      }

      if (button) {
        button.disabled = true;
        button.textContent = 'Envoi en cours...';
      }

      const emailPayload = toEmailPayload(formData);
      let backendError = null;

      try {
        await tryBackend(formData);
        form.reset();
        setFeedback('Votre message a ete envoye avec succes via le serveur.', true);
      } catch (error) {
        backendError = error;
        try {
          await tryEmailJs(emailPayload);
          form.reset();
          setFeedback('Serveur indisponible: message envoye via EmailJS.', true);
        } catch (emailError) {
          console.error('Contact submit failed', { backendError, emailError });
          const backendText = backendFailureText(backendError);
          const emailText = emailJsFailureText(emailError);
          setFeedback(`${backendText} ${emailText}`.trim(), false);
        }
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = originalText;
        }
      }
    });
  };

  document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('contactForm');
    if (!form) return;

    initEmailJs();
    bindFormSubmit(form);
    initFormAnimation();
  });
})();
