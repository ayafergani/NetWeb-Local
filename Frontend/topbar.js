(function () {
  if (window.__TOPBAR_INIT__) return;
  window.__TOPBAR_INIT__ = true;

  const root = document.getElementById('topbar-root');

  if (!root) {
    return;
  }

  const titleText = root.dataset.title || 'Tableau de bord';
  const subtitleText = root.dataset.subtitle || '';
  const notificationCount = Number(root.dataset.notifications || 3);

  // ========== RÉCUPÉRATION DE LA SESSION UTILISATEUR ==========
  function getSessionUser() {
    try {
      if (window.NetGuardAuth && typeof window.NetGuardAuth.getSession === 'function') {
        const s = window.NetGuardAuth.getSession();
        if (s) {
          // Enrichir avec email depuis localStorage si absent dans la session
          if (!s.email) s.email = localStorage.getItem('userEmail') || '';
          return s;
        }
      }
      const raw = localStorage.getItem('netguardSession');
      if (raw) {
        const s = JSON.parse(raw);
        if (!s.email) s.email = localStorage.getItem('userEmail') || '';
        return s;
      }
    } catch (e) {
      console.warn('Impossible de lire la session :', e);
    }
    return null;
  }

  const ROLE_LABELS = {
    'admin':          'Administrateur Système',
    'network_admin':  'Administrateur Réseau',
    'security_admin': 'Administrateur Sécurité',
    'auditor':        'Auditeur',
    'ADMIN':          'Administrateur Système',
    'NETWORK_ADMIN':  'Administrateur Réseau',
    'SECURITY_ADMIN': 'Administrateur Sécurité',
    'AUDITOR':        'Auditeur',
  };

  function getRoleLabel(role) {
    if (!role) return 'Inconnu';
    return ROLE_LABELS[role] || role;
  }

  function getRoleColor(role) {
    const r = (role || '').toLowerCase();
    if (r.includes('network'))  return '#0ea5e9';
    if (r.includes('security')) return '#f59e0b';
    if (r.includes('audit'))    return '#22c55e';
    if (r.includes('admin'))    return '#6366f1';
    return '#64748b';
  }

  function getInitials(username) {
    if (username) return username.slice(0, 2).toUpperCase();
    return '??';
  }

  // Déterminer s'il faut afficher le bouton de détection
  function shouldShowDetectionButton() {
    var role = '';
    try {
      if (window.NetGuardAuth && typeof window.NetGuardAuth.getRole === 'function') {
        role = (window.NetGuardAuth.getRole() || '').toUpperCase();
      } else {
        // Fallback : lire depuis la session localStorage
        var session = getSessionUser();
        if (session && session.role) role = session.role.toUpperCase();
      }
    } catch (e) {
      // Si erreur, on masque par sécurité
      return false;
    }
    // Afficher UNIQUEMENT pour ADMIN et SECURITY_ADMIN
    return role === 'ADMIN' || role === 'SECURITY_ADMIN';
  }

  const detectionButtonHTML = shouldShowDetectionButton() ? `
        <button type="button" id="topbar-detection-toggle" class="detection-btn detection-btn--start" aria-label="Lancer la detection" data-active="false">
          <svg id="topbar-detection-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M8 5v14l11-7z"></path>
          </svg>
          <span id="topbar-detection-label">Lancer la detection</span>
        </button>
  ` : '';

  root.innerHTML = `
    <header class="topbar">
      <div class="topbar-section">
        <div class="topbar-title" id="topbar-title"></div>
        <div class="topbar-subtitle" id="topbar-subtitle"></div>
      </div>

      <div class="topbar-actions">
        <div class="topbar-search-wrapper">
          <label class="topbar-search" for="topbar-search-input">
            <span class="sr-only">Rechercher</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input id="topbar-search-input" class="search-input" type="search" placeholder="Rechercher..." autocomplete="off" aria-controls="topbar-search-results" aria-expanded="false" />
          </label>
          <div id="topbar-search-results" class="topbar-search-results" role="listbox" hidden></div>
        </div>

        ${detectionButtonHTML}

        <div class="notif-wrapper">
          <button type="button" id="topbar-notif-button" class="icon-button" aria-label="Notifications">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M15 17h5l-1.405-1.405A2.032 2.032 0 0 1 18 14.158V11a6 6 0 0 0-12 0v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9" />
            </svg>
          </button>
          <span id="topbar-notif-count" class="notif-count"></span>

          <!-- Dropdown alertes -->
          <div id="topbar-alerts-dropdown" class="alerts-dropdown" hidden>
            <div class="alerts-dropdown-header">
              <span class="alerts-dropdown-title">Alertes récentes</span>
              <div style="display:flex;align-items:center;gap:6px;">
                <button type="button" id="topbar-notif-config-btn" class="alerts-config-btn" title="Configurer les notifications">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14">
                    <circle cx="12" cy="12" r="3"/>
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                  </svg>
                  Configurer
                </button>
                <a href="alerts.html" class="alerts-dropdown-viewall">Voir tout</a>
              </div>
            </div>
            <div id="topbar-alerts-list" class="alerts-dropdown-list">
              <div class="alerts-dropdown-empty">Aucune alerte</div>
            </div>
          </div>

          <!-- Modal configuration notifications -->
          <div id="topbar-notif-config-modal" class="notif-config-overlay" hidden>
            <div class="notif-config-card">
              <div class="notif-config-header">
                <div class="notif-config-header-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20">
                    <path d="M15 17h5l-1.405-1.405A2.032 2.032 0 0 1 18 14.158V11a6 6 0 0 0-12 0v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9"/>
                  </svg>
                </div>
                <div>
                  <h3 class="notif-config-title">Configuration des notifications</h3>
                  <p class="notif-config-subtitle">Paramètres du service de notification IDS</p>
                </div>
                <button type="button" id="notif-config-close" class="notif-config-close-btn" aria-label="Fermer">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="16" height="16">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              </div>

              <div class="notif-config-body">

                <!-- Section : Installation -->
                <div class="notif-config-section">
                  <div class="notif-config-section-label">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Installation du notifier
                  </div>
                  <p class="notif-config-hint">Installe le service de notification IDS en arrière-plan et le configure pour démarrer automatiquement.</p>
                  <button type="button" class="notif-config-action-btn notif-config-action-btn--primary" id="notif-run-install">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Installer le notifier
                  </button>
                </div>



              </div>

              <div class="notif-config-footer">
                <div class="notif-config-info">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                  Les actions s'exécutent en arrière-plan sur le serveur Windows.
                </div>
                <button type="button" id="notif-config-close-btn" class="notif-config-btn-close">Fermer</button>
              </div>
            </div>
          </div>
        </div>

        <button type="button" id="topbar-theme-toggle" class="icon-button theme-toggle" aria-label="Changer le theme">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"></path>
          </svg>
        </button>

        <div class="user-dropdown">
          <button type="button" id="topbar-user-button" class="icon-button user-avatar-btn" aria-label="Compte utilisateur">
            <span class="user-avatar-shell" aria-hidden="true">
              <svg class="user-avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 21a8 8 0 0 0-16 0"/>
                <circle cx="12" cy="8" r="4"/>
              </svg>
              <span id="topbar-user-initials" class="user-initials-badge">?</span>
              <span class="user-presence-dot"></span>
            </span>
            <svg class="user-avatar-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m6 9 6 6 6-6"/>
            </svg>
          </button>
          <div id="topbar-user-menu" class="user-menu" hidden>

            <!-- ===== HEADER ORBITAL ===== -->
            <div id="topbar-menu-user-info" class="user-menu-info">

              <!-- Avatar avec anneaux orbitaux SVG -->
              <div class="um-orbit-wrap">
                <svg class="um-orbit-svg" viewBox="0 0 108 108" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <g class="um-orbit-1" id="um-orbit-g1">
                    <circle cx="54" cy="54" r="48" stroke-width="1" stroke-dasharray="6 10" stroke-opacity="0.4" id="um-ring1-stroke"/>
                    <circle cx="54" cy="6" r="4" fill-opacity="0.7" id="um-ring1-dot"/>
                  </g>
                  <g class="um-orbit-2" id="um-orbit-g2">
                    <circle cx="54" cy="54" r="38" stroke-width="0.8" stroke-dasharray="4 14" stroke-opacity="0.3" id="um-ring2-stroke"/>
                    <circle cx="54" cy="16" r="3" fill-opacity="0.6" id="um-ring2-dot"/>
                  </g>
                </svg>
                <div id="topbar-menu-avatar" class="um-avatar">?</div>
                <span class="um-online-dot" title="En ligne"></span>
              </div>

              <!-- Nom + email + sticker rôle -->
              <div class="um-info">
                <div class="um-name-row">
                  <span id="topbar-menu-username" class="um-name">Chargement...</span>
                  <span class="um-check-badge" id="um-check-badge">
                    <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  </span>
                </div>
                <span class="um-email" id="topbar-menu-email">—</span>
                <span id="topbar-menu-role" class="um-role-pill">
                  <span class="um-role-icon" id="um-role-icon">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                    </svg>
                  </span>
                  <span id="topbar-menu-role-text">...</span>
                </span>
              </div>

            </div>

            <div class="user-menu-divider"></div>

            <button type="button" class="user-menu-item" id="topbar-menu-profile">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
              Mon profil
            </button>
            <button type="button" class="user-menu-item user-menu-item--danger" id="topbar-menu-logout">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
              Déconnexion
            </button>
          </div>
        </div>
      </div>
    </header>

    <div id="topbar-detection-toast" class="detection-toast" role="status" aria-live="polite" hidden></div>

    <!-- ===================== MODAL PROFIL ===================== -->
    <div id="topbar-profile-modal" class="ng-profile-overlay" role="dialog" aria-modal="true" aria-labelledby="ng-profile-title">
      <div class="ng-profile-card">

        <button type="button" id="profile-modal-close" class="ng-profile-close" aria-label="Fermer">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>

        <div class="ng-profile-header">
          <div class="ng-profile-header-bg" id="ng-header-bg"></div>
          <div class="ng-profile-avatar-wrap">
            <div class="ng-profile-avatar" id="ng-profile-avatar">??</div>
            <div class="ng-profile-status-dot" title="En ligne"></div>
          </div>
          <div class="ng-profile-header-info">
            <h2 class="ng-profile-name" id="ng-profile-title">Chargement…</h2>
            <span class="ng-profile-badge" id="ng-profile-badge">…</span>
          </div>
        </div>

        <div class="ng-profile-body">

          <div class="ng-info-row">
            <div class="ng-info-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/>
              </svg>
            </div>
            <div class="ng-info-content">
              <span class="ng-info-label">Nom d'utilisateur</span>
              <span class="ng-info-value" id="ng-profile-username">—</span>
            </div>
          </div>

          <div class="ng-info-row">
            <div class="ng-info-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
                <polyline points="22,6 12,13 2,6"/>
              </svg>
            </div>
            <div class="ng-info-content">
              <span class="ng-info-label">Adresse e-mail</span>
              <span class="ng-info-value" id="ng-profile-email">—</span>
            </div>
          </div>

          <div class="ng-info-row">
            <div class="ng-info-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
            </div>
            <div class="ng-info-content">
              <span class="ng-info-label">Rôle et permissions</span>
              <span class="ng-info-value" id="ng-profile-role">—</span>
            </div>
          </div>

          <div class="ng-info-row">
            <div class="ng-info-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
              </svg>
            </div>
            <div class="ng-info-content">
              <span class="ng-info-label">Session démarrée</span>
              <span class="ng-info-value" id="ng-profile-session">—</span>
            </div>
          </div>

        </div>

        <div class="ng-profile-footer">
          <button type="button" id="profile-modal-logout" class="ng-btn-logout">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/>
              <polyline points="16 17 21 12 16 7"/>
              <line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
            Déconnexion
          </button>
          <button type="button" id="profile-modal-close-btn" class="ng-btn-close">Fermer</button>
        </div>

      </div>
    </div>

  <style>
    /* ===== ORBITAL USER MENU ===== */

    .user-menu-info {
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px 16px 16px;
      gap: 10px;
      background: rgba(99,102,241,0.04);
      border-bottom: 1px solid rgba(99,102,241,0.12);
    }

    /* Conteneur orbit */
    .um-orbit-wrap {
      position: relative;
      width: 72px;
      height: 72px;
      flex-shrink: 0;
    }

    .um-orbit-svg {
      position: absolute;
      inset: -18px;
      width: 108px;
      height: 108px;
      pointer-events: none;
    }

    /* Animations orbites */
    @keyframes um-spin-cw  { from { transform: rotate(0deg);    } to { transform: rotate(360deg);  } }
    @keyframes um-spin-ccw { from { transform: rotate(0deg);    } to { transform: rotate(-360deg); } }

    .um-orbit-1 {
      transform-origin: 54px 54px;
      animation: um-spin-cw 9s linear infinite;
    }
    .um-orbit-2 {
      transform-origin: 54px 54px;
      animation: um-spin-ccw 14s linear infinite;
    }

    /* Avatar central */
    .um-avatar {
      position: absolute;
      inset: 0;
      border-radius: 50%;
      background: linear-gradient(145deg, #6366f1, #8b5cf6);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 21px;
      font-weight: 500;
      color: #fff;
      letter-spacing: 1px;
    }

    /* Point en ligne */
    .um-online-dot {
      position: absolute;
      bottom: 4px;
      right: 4px;
      width: 13px;
      height: 13px;
      background: #22c55e;
      border-radius: 50%;
      border: 2.5px solid #1a1c3a;
      box-shadow: 0 0 7px #22c55e88;
    }

    /* Nom + badge check */
    .um-info {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 5px;
    }

    .um-name-row {
      display: flex;
      align-items: center;
      gap: 5px;
    }

    .um-name {
      font-size: 14px;
      font-weight: 500;
      color: #f1f5f9;
    }

    .um-check-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 15px;
      height: 15px;
      border-radius: 50%;
      background: #6366f1;
      flex-shrink: 0;
    }

    .um-email {
      font-size: 11px;
      color: rgba(148,163,184,0.75);
    }

    /* Pill rôle */
    .um-role-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 11px 4px 6px;
      border-radius: 99px;
      background: rgba(99,102,241,0.15);
      border: 1px solid rgba(99,102,241,0.4);
      font-size: 11px;
      font-weight: 500;
      color: #a5b4fc;
      margin-top: 2px;
      transition: background 0.2s, color 0.2s, border-color 0.2s;
    }

    .um-role-icon {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #6366f1;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: background 0.2s;
    }
  </style>
  `;

  const titleEl      = document.getElementById('topbar-title');
  const subtitleEl   = document.getElementById('topbar-subtitle');
  const countEl      = document.getElementById('topbar-notif-count');
  const themeToggle  = document.getElementById('topbar-theme-toggle');
  const userButton   = document.getElementById('topbar-user-button');
  const userMenu     = document.getElementById('topbar-user-menu');
  const logoutBtn    = document.getElementById('topbar-menu-logout');
  const profileBtn   = document.getElementById('topbar-menu-profile');
  const profileModal = document.getElementById('topbar-profile-modal');
  const searchInput  = document.getElementById('topbar-search-input');
  const searchResults = document.getElementById('topbar-search-results');
  const detectionToggleBtn = document.getElementById('topbar-detection-toggle');
  const detectionToast     = document.getElementById('topbar-detection-toast');

  // ========== AVATAR + DROPDOWN ==========
  function initUserAvatar() {
    var user      = getSessionUser();
    var initials  = getInitials(user ? user.username : null);
    var roleColor = getRoleColor(user ? user.role : null);

    var initialsEl = document.getElementById('topbar-user-initials');
    if (initialsEl) {
      initialsEl.textContent = initials;
      initialsEl.style.background = 'linear-gradient(135deg, ' + roleColor + ', ' + roleColor + 'cc)';
    }

    var menuUsernameEl = document.getElementById('topbar-menu-username');
    var menuRoleTextEl = document.getElementById('topbar-menu-role-text');
    var menuRolePill   = document.getElementById('topbar-menu-role');
    var menuRoleIcon   = document.getElementById('um-role-icon');
    var menuCheckBadge = document.getElementById('um-check-badge');
    var menuAvatarEl   = document.getElementById('topbar-menu-avatar');
    var menuEmailEl    = document.getElementById('topbar-menu-email');

    if (menuUsernameEl) menuUsernameEl.textContent = (user && user.username) || 'Utilisateur';
    if (menuEmailEl)    menuEmailEl.textContent    = (user && user.email)    || '';
    if (menuRoleTextEl) menuRoleTextEl.textContent = getRoleLabel(user ? user.role : null);

    // Pill rôle couleur dynamique
    if (menuRolePill) {
      menuRolePill.style.background  = roleColor + '22';
      menuRolePill.style.borderColor = roleColor + '55';
      menuRolePill.style.color       = roleColor;
    }
    if (menuRoleIcon)   menuRoleIcon.style.background   = roleColor;
    if (menuCheckBadge) menuCheckBadge.style.background = roleColor;

    // Avatar central
    if (menuAvatarEl) {
      menuAvatarEl.textContent      = initials;
      menuAvatarEl.style.background = 'linear-gradient(145deg, ' + roleColor + ', ' + roleColor + 'cc)';
    }

    // Anneaux SVG colorés selon le rôle
    var ring1Stroke = document.getElementById('um-ring1-stroke');
    var ring1Dot    = document.getElementById('um-ring1-dot');
    var ring2Stroke = document.getElementById('um-ring2-stroke');
    var ring2Dot    = document.getElementById('um-ring2-dot');
    if (ring1Stroke) ring1Stroke.setAttribute('stroke', roleColor);
    if (ring1Dot)    ring1Dot.setAttribute('fill',   roleColor);
    if (ring2Stroke) ring2Stroke.setAttribute('stroke', roleColor);
    if (ring2Dot)    ring2Dot.setAttribute('fill',   roleColor);
  }

  // ========== REMPLIR LE MODAL PROFIL ==========
  function fillProfileModal() {
    var user      = getSessionUser();
    var initials  = getInitials(user ? user.username : null);
    var roleColor = getRoleColor(user ? user.role : null);

    // Fond coloré de l'en-tête
    var headerBg = document.getElementById('ng-header-bg');
    if (headerBg) {
      headerBg.style.background = 'linear-gradient(135deg, ' + roleColor + '28 0%, ' + roleColor + '08 100%)';
    }

    // Avatar avec initiales et ombre colorée
    var avatar = document.getElementById('ng-profile-avatar');
    if (avatar) {
      avatar.textContent     = initials;
      avatar.style.background  = 'linear-gradient(135deg, ' + roleColor + ' 0%, ' + roleColor + 'bb 100%)';
      avatar.style.boxShadow   = '0 8px 28px ' + roleColor + '55';
    }

    // Nom = username depuis la BDD
    var nameEl = document.getElementById('ng-profile-title');
    if (nameEl) nameEl.textContent = (user && user.username) || 'Utilisateur';

    // Badge rôle coloré
    var badgeEl = document.getElementById('ng-profile-badge');
    if (badgeEl) {
      badgeEl.textContent       = getRoleLabel(user ? user.role : null);
      badgeEl.style.background  = roleColor + '18';
      badgeEl.style.color       = roleColor;
      badgeEl.style.borderColor = roleColor + '50';
    }

    // Champs détaillés — toutes les données viennent de la session (BDD via /login)
    var usernameEl = document.getElementById('ng-profile-username');
    var emailEl    = document.getElementById('ng-profile-email');
    var roleEl     = document.getElementById('ng-profile-role');
    var sessionEl  = document.getElementById('ng-profile-session');

    if (usernameEl) usernameEl.textContent = (user && user.username) || '—';
    if (emailEl)    emailEl.textContent    = (user && user.email)    || 'Non renseigné';
    if (roleEl)     roleEl.textContent     = getRoleLabel(user ? user.role : null);

    if (sessionEl) {
      var loginTime = localStorage.getItem('netguard_login_time');
      if (!loginTime) {
        loginTime = Date.now().toString();
        localStorage.setItem('netguard_login_time', loginTime);
      }
      var d = new Date(Number(loginTime));
      sessionEl.textContent = d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
    }
  }

  function openProfileModal() {
    fillProfileModal();
    profileModal.classList.add('ng-profile-overlay--visible');
    closeUserMenu();
    setTimeout(function() {
      var closeBtn = document.getElementById('profile-modal-close');
      if (closeBtn) closeBtn.focus();
    }, 120);
  }

  function closeProfileModal() {
    profileModal.classList.remove('ng-profile-overlay--visible');
  }

  function doLogout() {
    if (window.NetGuardAuth && typeof window.NetGuardAuth.clearSession === 'function') {
      window.NetGuardAuth.clearSession();
    }
    localStorage.removeItem('netguard_login_time');
    window.location.href = 'login.html';
  }

  function updateNotificationCount(count) {
    var value = Number(count) || 0;
    countEl.textContent = value > 99 ? '99+' : value;
    countEl.style.display = value > 0 ? 'flex' : 'none';
  }

  function showDetectionToast(message, isError) {
    if (!detectionToast) return;
    detectionToast.textContent = message;
    detectionToast.classList.toggle('detection-toast--error', !!isError);
    detectionToast.hidden = false;
    clearTimeout(showDetectionToast.timer);
    showDetectionToast.timer = setTimeout(function () {
      detectionToast.hidden = true;
    }, 3500);
  }

  async function callDetection(endpoint, button, loadingText) {
    var originalHTML = button ? button.innerHTML : '';
    try {
      if (button) {
        button.disabled = true;
        var labelEl = button.querySelector('#topbar-detection-label') || button.querySelector('span');
        if (labelEl) labelEl.textContent = loadingText;
      }

      var res = await fetch(endpoint, { method: 'POST' });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.message || data.error || 'Erreur serveur');

      showDetectionToast(data.message || 'Action effectuée avec succès', false);
    } catch (e) {
      showDetectionToast(e.message || 'Impossible de contacter le serveur', true);
      // Restaurer le HTML original en cas d'erreur
      if (button) button.innerHTML = originalHTML;
      throw e; // Re-throw pour que le .then() du toggle ne soit pas appelé
    } finally {
      if (button) button.disabled = false;
    }
  }

  function setTheme(isDark) {
    document.documentElement.classList.toggle('dark-mode', isDark);
    document.body.classList.toggle('dark-mode', isDark);
    document.documentElement.classList.toggle('dark', isDark);
    document.body.classList.toggle('dark', isDark);

    if (themeToggle) {
      themeToggle.classList.toggle('toggle-active', isDark);
      themeToggle.setAttribute('aria-pressed', String(isDark));
      var svg = themeToggle.querySelector('svg');
      if (svg) {
        svg.innerHTML = isDark
          ? '<path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>'
          : '<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"></path>';
      }
    }
    localStorage.setItem('netguard-theme', isDark ? 'dark' : 'light');
    window.dispatchEvent(new CustomEvent('themeChanged', { detail: { isDark: isDark } }));
  }

  function toggleTheme() {
    var isDark = document.documentElement.classList.contains('dark-mode') || document.body.classList.contains('dark-mode');
    setTheme(!isDark);
  }

  function initTheme() {
    var savedTheme = localStorage.getItem('netguard-theme');
    var shouldBeDark = savedTheme === 'dark' || (!savedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches);
    setTheme(shouldBeDark);
  }

  function closeUserMenu() { userMenu.hidden = true; }
  function toggleUserMenu() { userMenu.hidden = !userMenu.hidden; }

  // ========== RECHERCHE GLOBALE ==========
  const SEARCH_ITEMS = [
    { id: 'dashboard', label: 'Tableau de bord', href: 'dashboard.html', keywords: ['accueil', 'home', 'overview', 'tableau de bord', 'reseau', 'réseau'] },
    { id: 'vlan', label: 'VLAN', href: 'vlan.html', keywords: ['vlans', 'reseau vlan', 'réseau vlan', 'quarantaine', 'isolation'] },
    { id: 'interfaces', label: 'Interfaces', href: 'interfaces.html', keywords: ['interface', 'ports', 'port', 'switchport', 'up', 'down', 'port security'] },
    { id: 'alerts', label: 'Alertes', href: 'alerts.html', keywords: ['alertes', 'alerte', 'snort', 'critique', 'securite', 'sécurité'] },
    { id: 'traffic', label: 'Trafic', href: 'traffic.html', keywords: ['trafic', 'network traffic', 'monitoring', 'bande passante'] },
    { id: 'configuration', label: 'Configuration', href: 'Configuration.html', keywords: ['config', 'regles', 'règles', 'rules', 'automation'] },
    { id: 'users', label: 'Utilisateurs', href: 'users.html', keywords: ['utilisateurs', 'user', 'roles', 'rôles', 'compte'] },
    { id: 'equipements', label: 'Equipements', href: 'equipements.html', keywords: ['equipement', 'équipement', 'switch', 'routeur', 'router'] },
    { id: 'logs', label: 'Journaux', href: 'logs.html', keywords: ['journal', 'audit', 'activites', 'activités', 'historique'] }
  ];

  let searchMatches = [];
  let activeSearchIndex = -1;

  function normalizeSearchText(value) {
    return String(value || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }

  function canOpenSearchItem(item) {
    if (!window.NetGuardAuth || typeof window.NetGuardAuth.canAccessPage !== 'function') {
      return true;
    }
    return window.NetGuardAuth.canAccessPage(item.id);
  }

  function getSearchableText(item) {
    return normalizeSearchText([item.label, item.id].concat(item.keywords || []).join(' '));
  }

  function closeSearchResults() {
    searchMatches = [];
    activeSearchIndex = -1;
    if (searchResults) {
      searchResults.hidden = true;
      searchResults.innerHTML = '';
    }
    if (searchInput) {
      searchInput.setAttribute('aria-expanded', 'false');
      searchInput.removeAttribute('aria-activedescendant');
    }
  }

  function openSearchItem(item) {
    if (!item || !canOpenSearchItem(item)) return;
    window.location.href = item.href;
  }

  function setActiveSearchIndex(index) {
    activeSearchIndex = index;
    var options = searchResults ? searchResults.querySelectorAll('.topbar-search-result') : [];
    options.forEach(function(option, optionIndex) {
      var isActive = optionIndex === activeSearchIndex;
      option.classList.toggle('active', isActive);
      option.setAttribute('aria-selected', String(isActive));
      if (isActive && searchInput) {
        searchInput.setAttribute('aria-activedescendant', option.id);
      }
    });
  }

  function renderSearchResults(query) {
    if (!searchInput || !searchResults) return;

    var normalizedQuery = normalizeSearchText(query).trim();
    if (!normalizedQuery) {
      closeSearchResults();
      return;
    }

    searchMatches = SEARCH_ITEMS
      .filter(canOpenSearchItem)
      .filter(function(item) {
        return getSearchableText(item).includes(normalizedQuery);
      })
      .slice(0, 6);

    if (searchMatches.length === 0) {
      searchResults.innerHTML = '<div class="topbar-search-empty">Aucun resultat</div>';
      searchResults.hidden = false;
      searchInput.setAttribute('aria-expanded', 'true');
      activeSearchIndex = -1;
      return;
    }

    searchResults.innerHTML = searchMatches.map(function(item, index) {
      return '<button type="button" id="topbar-search-option-' + index + '" class="topbar-search-result" role="option" aria-selected="false" data-index="' + index + '">' +
        '<span class="topbar-search-result-title">' + item.label + '</span>' +
        '<span class="topbar-search-result-path">' + item.href + '</span>' +
      '</button>';
    }).join('');

    searchResults.hidden = false;
    searchInput.setAttribute('aria-expanded', 'true');
    setActiveSearchIndex(0);
  }

  function initGlobalSearch() {
    if (!searchInput || !searchResults) return;

    searchInput.addEventListener('input', function() {
      renderSearchResults(searchInput.value);
    });

    searchInput.addEventListener('focus', function() {
      renderSearchResults(searchInput.value);
    });

    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        openSearchItem(searchMatches[activeSearchIndex] || searchMatches[0]);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (searchMatches.length) setActiveSearchIndex((activeSearchIndex + 1) % searchMatches.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (searchMatches.length) setActiveSearchIndex((activeSearchIndex - 1 + searchMatches.length) % searchMatches.length);
      } else if (e.key === 'Escape') {
        closeSearchResults();
      }
    });

    searchResults.addEventListener('mousedown', function(e) {
      var result = e.target.closest('.topbar-search-result');
      if (!result) return;
      e.preventDefault();
      openSearchItem(searchMatches[Number(result.dataset.index)]);
    });
  }

  // ========== INIT ==========
  titleEl.textContent    = titleText;
  subtitleEl.textContent = subtitleText;
  subtitleEl.style.display = subtitleText ? 'block' : 'none';
  updateNotificationCount(notificationCount);
  initTheme();
  initUserAvatar();
  initGlobalSearch();

  // ========== INITIALISER LA VISIBILITÉ DU BOUTON DE DÉTECTION ==========
  function initDetectionButtonVisibility() {
    if (!detectionToggleBtn) return;
    
    // Obtenir le rôle directement depuis NetGuardAuth
    var role = '';
    if (window.NetGuardAuth && typeof window.NetGuardAuth.getRole === 'function') {
      role = (window.NetGuardAuth.getRole() || '').toUpperCase();
    }
    
    // Masquer le bouton pour les rôles "AUDITOR" et "NETWORK_ADMIN"
    if (role === 'AUDITOR' || role === 'NETWORK_ADMIN') {
      detectionToggleBtn.style.display = 'none';
    }
  }

  initDetectionButtonVisibility();

  // ========== ÉVÉNEMENTS ==========
  window.addEventListener('storage', function (e) {
    if (e.key === 'netguard-theme') setTheme(e.newValue === 'dark');
  });

  if (themeToggle) themeToggle.addEventListener('click', toggleTheme);
  if (detectionToggleBtn) {
    detectionToggleBtn.addEventListener('click', function () {
      var isActive = detectionToggleBtn.dataset.active === 'true';
      if (!isActive) {
        // Passer en mode "actif" → appeler start, switcher vers Stop
        callDetection('/start-detection', detectionToggleBtn, 'Lancement...').then(function () {
          detectionToggleBtn.dataset.active = 'true';
          detectionToggleBtn.classList.remove('detection-btn--start');
          detectionToggleBtn.classList.add('detection-btn--stop');
          detectionToggleBtn.setAttribute('aria-label', 'Arrêter la détection');
          document.getElementById('topbar-detection-label').textContent = 'Arreter la detection';
          document.getElementById('topbar-detection-icon').innerHTML =
            '<rect x="7" y="7" width="10" height="10" rx="1.5"></rect>';
          document.getElementById('topbar-detection-icon').setAttribute('fill', 'none');
          document.getElementById('topbar-detection-icon').setAttribute('stroke', 'currentColor');
          document.getElementById('topbar-detection-icon').setAttribute('stroke-width', '2.4');
          document.getElementById('topbar-detection-icon').setAttribute('stroke-linecap', 'round');
          document.getElementById('topbar-detection-icon').setAttribute('stroke-linejoin', 'round');
        });
      } else {
        // Passer en mode "inactif" → appeler stop, switcher vers Start
        callDetection('/stop-detection', detectionToggleBtn, 'Arrêt...').then(function () {
          detectionToggleBtn.dataset.active = 'false';
          detectionToggleBtn.classList.remove('detection-btn--stop');
          detectionToggleBtn.classList.add('detection-btn--start');
          detectionToggleBtn.setAttribute('aria-label', 'Lancer la détection');
          document.getElementById('topbar-detection-label').textContent = 'Lancer la detection';
          var icon = document.getElementById('topbar-detection-icon');
          icon.setAttribute('fill', 'currentColor');
          icon.removeAttribute('stroke');
          icon.removeAttribute('stroke-width');
          icon.removeAttribute('stroke-linecap');
          icon.removeAttribute('stroke-linejoin');
          icon.innerHTML = '<path d="M8 5v14l11-7z"></path>';
        });
      }
    });
  }

  userButton.addEventListener('click', function (e) {
    e.stopPropagation();
    toggleUserMenu();
  });

  profileBtn.addEventListener('click', openProfileModal);
  logoutBtn.addEventListener('click', doLogout);

  document.getElementById('profile-modal-logout').addEventListener('click', doLogout);
  document.getElementById('profile-modal-close').addEventListener('click', closeProfileModal);
  document.getElementById('profile-modal-close-btn').addEventListener('click', closeProfileModal);

  profileModal.addEventListener('click', function (e) {
    if (e.target === profileModal) closeProfileModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeProfileModal(); closeUserMenu(); }
  });

  document.addEventListener('click', function (e) {
    if (userMenu && !userMenu.contains(e.target) && e.target !== userButton) closeUserMenu();
    if (searchResults && searchInput && !searchResults.contains(e.target) && e.target !== searchInput && !e.target.closest('.topbar-search')) closeSearchResults();
  });

  // ========== MODAL CONFIGURATION NOTIFICATIONS ==========
  var _notifConfigModal  = document.getElementById('topbar-notif-config-modal');
  var _notifConfigOpenBtn = document.getElementById('topbar-notif-config-btn');
  var _notifConfigCloseBtn = document.getElementById('notif-config-close');
  var _notifConfigCloseBtnFooter = document.getElementById('notif-config-close-btn');

  // Rôles autorisés à accéder à la configuration des notifications
  function _canAccessNotifConfig() {
    try {
      var role = '';
      if (window.NetGuardAuth && typeof window.NetGuardAuth.getRole === 'function') {
        role = (window.NetGuardAuth.getRole() || '').toUpperCase();
      } else {
        var raw = localStorage.getItem('netguardSession');
        if (raw) { role = (JSON.parse(raw).role || '').toUpperCase(); }
      }
      return role === 'ADMIN' || role === 'SECURITY_ADMIN';
    } catch(e) { return false; }
  }

  // Masquer le bouton "Configurer" si le rôle n'est pas autorisé
  if (_notifConfigOpenBtn && !_canAccessNotifConfig()) {
    _notifConfigOpenBtn.style.display = 'none';
  }

  function _openNotifConfig() {
    if (!_canAccessNotifConfig()) {
      // Afficher un message d'accès refusé discret dans le dropdown
      var _deniedMsg = document.getElementById('_notif-access-denied');
      if (!_deniedMsg) {
        _deniedMsg = document.createElement('div');
        _deniedMsg.id = '_notif-access-denied';
        _deniedMsg.style.cssText = 'margin:8px 12px;padding:8px 12px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;font-size:11px;color:#dc2626;display:flex;align-items:center;gap:6px;';
        _deniedMsg.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg> Accès réservé aux administrateurs.';
        var _alertsList2 = document.getElementById('topbar-alerts-list');
        if (_alertsList2 && _alertsList2.parentNode) {
          _alertsList2.parentNode.insertBefore(_deniedMsg, _alertsList2);
          setTimeout(function() { if (_deniedMsg && _deniedMsg.parentNode) _deniedMsg.parentNode.removeChild(_deniedMsg); }, 3000);
        }
      }
      return;
    }
    if (_notifConfigModal) _notifConfigModal.hidden = false;
    if (typeof _closeAlertsDropdown === 'function') _closeAlertsDropdown();
  }
  function _closeNotifConfig() {
    if (_notifConfigModal) _notifConfigModal.hidden = true;
  }

  if (_notifConfigOpenBtn)        _notifConfigOpenBtn.addEventListener('click', function(e) { e.stopPropagation(); _openNotifConfig(); });
  if (_notifConfigCloseBtn)       _notifConfigCloseBtn.addEventListener('click', _closeNotifConfig);
  if (_notifConfigCloseBtnFooter) _notifConfigCloseBtnFooter.addEventListener('click', _closeNotifConfig);

  if (_notifConfigModal) {
    _notifConfigModal.addEventListener('click', function(e) {
      if (e.target === _notifConfigModal) _closeNotifConfig();
    });
  }

var _installBtn = document.getElementById('notif-run-install');
  if (_installBtn) {
    _installBtn.addEventListener('click', function() { _launchPBat('1', null, _installBtn, 'Installer le notifier', 'Installation lancée ✅'); });
  }

function _launchPBat(option, params, btn, originalLabel, successLabel) {
    var body = { option: option };
    if (params) body.params = params;

    // État chargement
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.7';
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="animation:spin 1s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> En cours…';
    }

    fetch(window.NetGuardAuth.buildApiUrl('/api/run-pbat'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d && d.success) {
        showDetectionToast(successLabel || 'Opération réussie ✅', false);
        _closeNotifConfig();
      } else {
        showDetectionToast((d && d.message) || 'Erreur lors du lancement du service', true);
      }
    })
    .catch(function() {
      showDetectionToast('Impossible de contacter le serveur d application', true);
    })
    .finally(function() {
      if (btn) {
        btn.disabled = false;
        btn.style.opacity = '';
        // Restaurer le label d'origine avec son icône
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> ' + (originalLabel || 'Installer le notifier');
      }
    });
  }

  // ========== ALERTES TEMPS RÉEL ==========
  var _lastAlertCount = null;
  var _alertsDropdown = document.getElementById('topbar-alerts-dropdown');
  var _alertsList     = document.getElementById('topbar-alerts-list');
  var _notifButton    = document.getElementById('topbar-notif-button');

  function _severityColor(severity) {
    var s = (severity || '').toLowerCase();
    if (s === 'critique' || s === 'critical' || s === 'high' || s === '1') return '#ef4444';
    if (s === 'moyen'    || s === 'medium'   || s === 'moderate' || s === '2') return '#f59e0b';
    return '#22c55e';
  }

  function _severityLabel(severity) {
    var s = (severity || '').toLowerCase();
    if (s === 'critique' || s === 'critical' || s === 'high' || s === '1') return 'Critique';
    if (s === 'moyen'    || s === 'medium'   || s === 'moderate' || s === '2') return 'Moyen';
    return 'Faible';
  }

  function _renderAlertsList(alerts) {
    if (!_alertsList) return;
    if (!alerts || alerts.length === 0) {
      _alertsList.innerHTML = '<div class="alerts-dropdown-empty">Aucune alerte récente</div>';
      return;
    }
    _alertsList.innerHTML = alerts.slice(0, 8).map(function(a) {
      var color = _severityColor(a.severity || a.priority || a.gravite || '');
      var label = _severityLabel(a.severity || a.priority || a.gravite || '');
      var msg   = a.message || a.msg || a.classification || a.description || 'Alerte détectée';
      var src   = a.src_ip  || a.source_ip || a.source || '';
      var ts    = a.timestamp || a.date || a.created_at || '';
      var time  = '';
      if (ts) {
        try {
          var d = new Date(ts);
          time = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
        } catch(e) { time = ts; }
      }
      return '<div class="alerts-dropdown-item">' +
        '<span class="alert-dot" style="background:' + color + '"></span>' +
        '<div class="alert-item-body">' +
          '<span class="alert-item-msg">' + msg + '</span>' +
          '<span class="alert-item-meta">' +
            '<span class="alert-item-badge" style="color:' + color + ';border-color:' + color + '40;background:' + color + '12">' + label + '</span>' +
            (src  ? '<span class="alert-item-ip">'   + src  + '</span>' : '') +
            (time ? '<span class="alert-item-time">'  + time + '</span>' : '') +
          '</span>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function _toggleAlertsDropdown(e) {
    e.stopPropagation();
    if (!_alertsDropdown) return;
    var isHidden = _alertsDropdown.hidden;
    _alertsDropdown.hidden = !isHidden;
    if (!isHidden) return;
    // Marquer comme lu : reset badge
    updateNotificationCount(0);
    _lastAlertCount = null; // sera recalculé au prochain poll
  }

  function _closeAlertsDropdown() {
    if (_alertsDropdown) _alertsDropdown.hidden = true;
  }

  if (_notifButton) {
    _notifButton.addEventListener('click', _toggleAlertsDropdown);
  }

  document.addEventListener('click', function(e) {
    if (_alertsDropdown && !_alertsDropdown.hidden) {
      if (!_alertsDropdown.contains(e.target) && e.target !== _notifButton) {
        _closeAlertsDropdown();
      }
    }
  });

  // Poll API alertes toutes les 5 secondes
  async function _pollAlerts() {
    // Ne pas requêter si l'onglet est masqué, si non-authentifié ou si hors-ligne
    if (document.hidden || !window.NetGuardAuth?.isAuthenticated() || !navigator.onLine) {
      setTimeout(_pollAlerts, 30000);
      return;
    }

    if (_isHeavyDataPage()) {
      setTimeout(_pollAlerts, 60000);
      return;
    }

    try {
      const headers = { ...window.NetGuardAuth.getAuthHeaders() };
      delete headers['Content-Type'];

      // Récupérer les alertes récentes
      var res = await fetch(window.NetGuardAuth.buildApiUrl('/api/alerts?limit=8&sort=desc'), { headers: headers });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();

      _renderAlertsList(data.alerts || data.data || data.results || []);

      // Récupérer le total pour le badge (Uniquement si le menu est fermé pour économiser une requête)
      if (_alertsDropdown && _alertsDropdown.hidden) {
        var statsRes = await fetch(window.NetGuardAuth.buildApiUrl('/api/stats'), { headers: headers });
        var statsData = await statsRes.json();
        var total = parseInt((statsData.stats || statsData).total) || 0;
        if (_lastAlertCount === null) _lastAlertCount = total;
        updateNotificationCount(Math.max(0, total - _lastAlertCount));
      }
    } catch(e) {
      // Silencieux — API peut être indisponible
    } finally {
      // Intervalle allonge pour limiter la charge du backend local.
      setTimeout(_pollAlerts, 30000);
    }
  }

  function _isHeavyDataPage() {
    var path = window.location.pathname.toLowerCase();
    return path.includes('dashboard.html') || path.includes('configuration.html');
  }

  _pollAlerts();

  // API publique : permet à alerts.html de réinitialiser le badge topbar
  window.resetTopbarAlertBadge = function() {
    fetch(window.NetGuardAuth.buildApiUrl('/api/stats')).then(function(r) { return r.json(); }).then(function(d) {
      if (d.success || d.stats) _lastAlertCount = parseInt((d.stats || d).total) || 0;
    }).catch(function(){});
    updateNotificationCount(0);
  };

  // ========== API PUBLIQUE ==========
  window.setPageTitle = function (title, subtitle) {
    titleEl.textContent    = title || 'Tableau de bord';
    subtitleEl.textContent = subtitle || '';
    subtitleEl.style.display = subtitle ? 'block' : 'none';
  };
  window.setNotificationCount = function (count) { updateNotificationCount(count); };
  window.toggleTheme  = toggleTheme;
  window.setTheme     = setTheme;
  window.openProfileModal = openProfileModal;
})();
