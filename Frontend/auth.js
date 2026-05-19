(function () {
  // ========================================================
  // MODE DESIGN : Mettre à true pour désactiver la sécurité
  const DEV_MODE = false; 
  // ========================================================

  const STORAGE_KEY = 'netguardSession';
  const DEFAULT_API_BASE_URL = 'http://127.0.0.1:5000';
  const ROLES = {
    ADMIN: 'ADMIN',
    NETWORK_ADMIN: 'NETWORK_ADMIN',
    SECURITY_ADMIN: 'SECURITY_ADMIN',
    AUDITOR: 'AUDITOR'
  };

  const PAGE_ACCESS = {
    dashboard: [ROLES.ADMIN, ROLES.NETWORK_ADMIN, ROLES.SECURITY_ADMIN, ROLES.AUDITOR],
    vlan: [ROLES.ADMIN, ROLES.NETWORK_ADMIN],
    interfaces: [ROLES.ADMIN, ROLES.NETWORK_ADMIN],
    alerts: [ROLES.ADMIN, ROLES.SECURITY_ADMIN, ROLES.AUDITOR],
    traffic: [ROLES.ADMIN, ROLES.SECURITY_ADMIN, ROLES.AUDITOR],
    configuration: [ROLES.ADMIN, ROLES.SECURITY_ADMIN],
    users: [ROLES.ADMIN],
    equipements: [ROLES.ADMIN, ROLES.NETWORK_ADMIN],
    logs: [ROLES.ADMIN, ROLES.AUDITOR]
  };

  const DEFAULT_PAGE_BY_ROLE = {
    [ROLES.ADMIN]: 'dashboard.html',
    [ROLES.NETWORK_ADMIN]: 'dashboard.html',
    [ROLES.SECURITY_ADMIN]: 'dashboard.html',
    [ROLES.AUDITOR]: 'dashboard.html'
  };

  // NOTE: Removed hardcoded users. Password reset now uses backend endpoints.

  function getSession() {
    if (DEV_MODE) {
      return { 
        username: 'dev_design', 
        name: 'Designer', 
        role: ROLES.ADMIN, 
        token: 'dev-token' 
      };
    }
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function setSession(session) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    localStorage.setItem('userRole', session.role);
    localStorage.setItem('userName', session.name);
    localStorage.setItem('userId', session.username);
    if (session.email) {
      localStorage.setItem('userEmail', session.email);
    }
    if (session.token) {
      localStorage.setItem('jwtToken', session.token);
    }
  }

  function clearSession() {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem('userRole');
    localStorage.removeItem('userName');
    localStorage.removeItem('userId');
    localStorage.removeItem('userEmail');
    localStorage.removeItem('jwtToken');
  }

  function normalizeApiBaseUrl(value) {
    return String(value || '').trim().replace(/\/+$/, '');
  }

  function getApiBaseUrl() {
    return DEFAULT_API_BASE_URL;
  }

  function setApiBaseUrl(value) {
    return normalizeApiBaseUrl(value) || DEFAULT_API_BASE_URL;
  }

  function buildApiUrl(path) {
    const normalizedPath = String(path || '').startsWith('/') ? path : `/${path || ''}`;
    return `${getApiBaseUrl()}${normalizedPath}`;
  }

  function buildFetchOptions(method = 'GET', body = null, customHeaders = {}) {
    const options = {
      method,
      headers: {
        ...customHeaders
      }
    };
    if (body) {
      if (!(body instanceof FormData) && !options.headers['Content-Type']) {
        options.headers['Content-Type'] = 'application/json';
      }
      options.body = body instanceof FormData || typeof body === 'string'
        ? body
        : JSON.stringify(body);
    }
    return options;
  }

  // API Fetch wrapper for the local Flask backend.
  async function apiFetch(path, options = {}) {
    const url = buildApiUrl(path);
    const method = options.method || 'GET';
    const body = options.body;
    const customHeaders = options.headers || {};
    const authHeaders = getAuthHeaders();
    const fetchOptions = {
      method,
      headers: {
        ...authHeaders,
        ...customHeaders
      }
    };
    if (!body && !customHeaders['Content-Type']) {
      delete fetchOptions.headers['Content-Type'];
    }
    if (body) {
      if (!(body instanceof FormData) && !fetchOptions.headers['Content-Type']) {
        fetchOptions.headers['Content-Type'] = 'application/json';
      }
      fetchOptions.body = body instanceof FormData || typeof body === 'string'
        ? body
        : JSON.stringify(body);
    }
    return fetch(url, fetchOptions);
  }

  function getRole() {
    const session = getSession();
    return session ? session.role : null;
  }

  function isAuthenticated() {
    return Boolean(getSession());
  }

  function canAccessPage(pageId, role) {
    const resolvedRole = role || getRole();
    const allowedRoles = PAGE_ACCESS[pageId] || [];
    return allowedRoles.includes(resolvedRole);
  }

  function getAllowedPages(role) {
    const resolvedRole = role || getRole();
    return Object.keys(PAGE_ACCESS).filter((pageId) => canAccessPage(pageId, resolvedRole));
  }

  function getDefaultPage(role) {
    const safeRole = String(role || '').toUpperCase(); // Force la lecture en majuscules
    return DEFAULT_PAGE_BY_ROLE[safeRole] || 'login.html';
  }

  function redirectToDefault(role) {
    window.location.replace(getDefaultPage(role || getRole()));
  }

  function requirePageAccess(pageId) {
    if (DEV_MODE) {
      return true; // Désactive le "videur" pour le design
    }

    const session = getSession();

    if (!session) {
      window.location.replace('login.html');
      return false;
    }

    if (!canAccessPage(pageId, session.role)) {
      redirectToDefault(session.role);
      return false;
    }

    return true;
  }

  // Demande de réinitialisation : appelle le backend pour générer et envoyer un token de reset
  async function requestPasswordReset(email) {
    if (!email) return { success: false, error: 'Email requis' };

    if (DEV_MODE) {
      console.log('[DEV] Simuler requestPasswordReset pour', email);
      return { success: true, message: 'Simulé : si l\'email existe, un lien a été envoyé.' };
    }

    try {
      const resp = await fetch(buildApiUrl('/forgot-password'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });

      const data = await resp.json();
      if (resp.ok) {
        return { success: true, message: data.message };
      }
      return { success: false, error: data.error || 'Erreur lors de la requête' };
    } catch (err) {
      console.error('Erreur requestPasswordReset:', err);
      return { success: false, error: 'Impossible de joindre le serveur' };
    }
  }

  // Effectuer la réinitialisation finale : envoie le token et le nouveau mot de passe au backend
  async function performPasswordReset(token, newPassword) {
    if (!token || !newPassword) return { success: false, error: 'Token et nouveau mot de passe requis' };

    if (DEV_MODE) {
      console.log('[DEV] Simuler performPasswordReset', token, newPassword);
      return { success: true, message: 'Mot de passe réinitialisé (simulé).' };
    }

    try {
      const resp = await fetch(buildApiUrl('/reset-password'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: newPassword })
      });

      const data = await resp.json();
      if (resp.ok) return { success: true, message: data.message };
      return { success: false, error: data.error || 'Erreur lors de la réinitialisation' };
    } catch (err) {
      console.error('Erreur performPasswordReset:', err);
      return { success: false, error: 'Impossible de joindre le serveur' };
    }
  }

  async function authenticate(username, password) {
    try {
      const response = await fetch(buildApiUrl('/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      
      const data = await response.json();
      
      if (response.ok) {
        // On s'assure que le rôle est bien en majuscules et sans espaces (ex: "admin" -> "ADMIN")
        const safeRole = (data.role || '').trim().toUpperCase();
        
        return {
          success: true,
          user: {
            username: data.username || username,  // ← username réel depuis la BDD
            name:     data.username || username,  // ← idem
            email:    data.email    || '',        // ← email réel depuis la BDD
            role:     safeRole,
            token:    data.access_token
          }
        };
      } else {
        return { success: false, error: data.error || 'Identifiant ou mot de passe incorrect' };
      }
    } catch (error) {
      console.error("Erreur backend:", error);
      return { success: false, error: 'Impossible de se connecter au serveur' };
    }
  }

  function getAuthHeaders() {
    if (DEV_MODE) {
      return { 
        'Content-Type': 'application/json',
        'Authorization': 'Bearer dev-token'
      };
    }
    const token = localStorage.getItem('jwtToken');
    return {
      'Content-Type': 'application/json',
      'Authorization': token ? `Bearer ${token}` : ''
    };
  }

  window.NetGuardAuth = {
    ROLES,
    PAGE_ACCESS,
    getSession,
    setSession,
    clearSession,
    getRole,
    isAuthenticated,
    canAccessPage,
    getAllowedPages,
    getDefaultPage,
    redirectToDefault,
    requirePageAccess,
    authenticate,
    getAuthHeaders,
    getApiBaseUrl,
    setApiBaseUrl,
    buildApiUrl,
    buildFetchOptions,
    apiFetch,
    requestPasswordReset,
    performPasswordReset
  };
})();
