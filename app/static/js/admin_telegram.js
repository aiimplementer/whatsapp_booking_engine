/* Telegram bot connect/disconnect widget on the settings page.
 * Talks to /api/v1/telegram/{status,connect,disconnect} — separate from
 * admin_settings.js's tenant PATCH, since the bot token is a secret credential
 * validated server-side (via Telegram's getMe) rather than a plain field
 * saved alongside the rest of the business settings form. */

const tgEls = {
  skeleton: document.getElementById('telegram-skeleton'),
  disconnectedBox: document.getElementById('telegram-disconnected'),
  connectedBox: document.getElementById('telegram-connected'),
  banner: document.getElementById('telegram-banner'),
  ok: document.getElementById('telegram-ok'),
  tokenInput: document.getElementById('tg-token'),
  connectBtn: document.getElementById('tg-connect-btn'),
  disconnectBtn: document.getElementById('tg-disconnect-btn'),
  username: document.getElementById('tg-username'),
};

async function loadTelegramStatus() {
  try {
    const status = await Api.get('/api/v1/telegram/status');
    tgEls.skeleton.hidden = true;
    if (status.connected) {
      tgEls.connectedBox.classList.remove('hidden');
      tgEls.disconnectedBox.classList.add('hidden');
      tgEls.username.textContent = status.bot_username ? `@${status.bot_username}` : 'Connected';
    } else {
      tgEls.disconnectedBox.classList.remove('hidden');
      tgEls.connectedBox.classList.add('hidden');
    }
  } catch (err) {
    tgEls.skeleton.hidden = true;
    showBanner(tgEls.banner, err.detail || err.message);
  }
}

tgEls.connectBtn.addEventListener('click', async () => {
  const token = tgEls.tokenInput.value.trim();
  showBanner(tgEls.banner, null);
  showBanner(tgEls.ok, null);
  if (!token) {
    showBanner(tgEls.banner, 'Paste the bot token from @BotFather first.');
    return;
  }
  tgEls.connectBtn.disabled = true;
  try {
    await Api.post('/api/v1/telegram/connect', { bot_token: token });
    tgEls.tokenInput.value = '';
    showBanner(tgEls.ok, 'Telegram bot connected.');
    await loadTelegramStatus();
  } catch (err) {
    showBanner(tgEls.banner, err.detail || err.message);
  } finally {
    tgEls.connectBtn.disabled = false;
  }
});

tgEls.disconnectBtn.addEventListener('click', async () => {
  const confirmed = await confirmDialog(
    "Disconnect the Telegram bot? Customers messaging it won't be able to book until you reconnect.",
    { okLabel: 'Disconnect', danger: true }
  );
  if (!confirmed) return;
  showBanner(tgEls.banner, null);
  showBanner(tgEls.ok, null);
  tgEls.disconnectBtn.disabled = true;
  try {
    await Api.post('/api/v1/telegram/disconnect');
    showBanner(tgEls.ok, 'Telegram bot disconnected.');
    await loadTelegramStatus();
  } catch (err) {
    showBanner(tgEls.banner, err.detail || err.message);
  } finally {
    tgEls.disconnectBtn.disabled = false;
  }
});

loadTelegramStatus();
