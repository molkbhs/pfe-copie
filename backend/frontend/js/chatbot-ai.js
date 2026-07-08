// Shared chatbot client for Dash / Analyse / Assistance
(function initChatbotAiClient(global) {
  const AI_ENDPOINT = '/api/chatbot/ask-ai';
  const CLASSIC_ENDPOINT = '/api/chatbot/ask';

  function safeParse(jsonText) {
    try {
      return JSON.parse(jsonText);
    } catch (_) {
      return null;
    }
  }

  function resolveAuthHeaders(json) {
    if (typeof global.authHeaders === 'function') {
      return global.authHeaders(json === true);
    }

    if (global.UICore?.authHeaders) {
      return global.UICore.authHeaders({ json: json === true });
    }

    const headers = {};
    let token = '';
    const user =
      global.UICore?.getUser?.({ migrate: true }) ||
      safeParse(global.localStorage?.getItem('user') || 'null') ||
      safeParse(global.sessionStorage?.getItem('user') || 'null');

    if (user?.token) token = String(user.token);
    if (token) headers.Authorization = `Bearer ${token}`;
    if (json === true) headers['Content-Type'] = 'application/json';
    return headers;
  }

  async function parseJsonSafe(response) {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  function buildPayload(message, page, extraContext) {
    const payload = {
      question: message,
      message,
      page: page || 'general',
    };

    if (extraContext && typeof extraContext === 'object') {
      Object.assign(payload, extraContext);
    }

    return payload;
  }

  function appendChatMessage(target, text, role) {
    const host = typeof target === 'string' ? document.querySelector(target) : target;
    if (!host) return;

    const el = document.createElement('div');
    el.className = `chat-msg ${role || 'bot'}`;
    el.textContent = String(text || '');
    host.appendChild(el);
    host.scrollTop = host.scrollHeight;
  }

  async function fallbackClassicChatbot(message, page, extraContext) {
    try {
      const payload = buildPayload(message, page, extraContext);
      console.log('Chatbot request:', payload);

      const response = await fetch(CLASSIC_ENDPOINT, {
        method: 'POST',
        cache: 'no-store',
        headers: resolveAuthHeaders(true),
        body: JSON.stringify(payload),
      });

      const data = await parseJsonSafe(response);
      console.log('Chatbot response:', data);

      if (response.status === 401 || response.status === 403) {
        const authError = new Error(data?.error || 'Auth requis');
        authError.code = 'AUTH_REQUIRED';
        authError.status = response.status;
        throw authError;
      }

      const answer = data?.answer || data?.response || data?.reply || data?.message || '';
      if (response.ok && data?.success !== false && answer) {
        return String(answer);
      }
      return 'Assistant temporairement indisponible.';
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') throw error;
      return 'Erreur de connexion, réessayez.';
    }
  }

  async function sendChatbotMessage(message, page, extraContext) {
    const cleanMessage = String(message || '').trim();
    if (!cleanMessage) return 'Posez une question plus précise.';

    const payload = buildPayload(cleanMessage, page, extraContext);
    console.log('Chatbot request:', payload);

    try {
      const response = await fetch(AI_ENDPOINT, {
        method: 'POST',
        cache: 'no-store',
        headers: resolveAuthHeaders(true),
        body: JSON.stringify(payload),
      });

      const data = await parseJsonSafe(response);
      console.log('Chatbot response:', data);

      if (response.status === 401 || response.status === 403) {
        const authError = new Error(data?.error || 'Auth requis');
        authError.code = 'AUTH_REQUIRED';
        authError.status = response.status;
        throw authError;
      }

      const answer = data?.answer || '';
      if (response.ok && data?.success !== false && answer) {
        return String(answer);
      }

      return fallbackClassicChatbot(cleanMessage, page, extraContext);
    } catch (error) {
      if (error?.code === 'AUTH_REQUIRED') throw error;
      return fallbackClassicChatbot(cleanMessage, page, extraContext);
    }
  }

  global.ChatbotAI = {
    sendChatbotMessage,
    fallbackClassicChatbot,
    appendUserMessage: function appendUserMessage(target, text) {
      appendChatMessage(target, text, 'user');
    },
    appendBotMessage: function appendBotMessage(target, text, role) {
      appendChatMessage(target, text, role || 'bot');
    },
  };

  // Optional global aliases for existing inline code.
  global.sendChatbotMessage = sendChatbotMessage;
  global.fallbackClassicChatbot = fallbackClassicChatbot;
})(window);

