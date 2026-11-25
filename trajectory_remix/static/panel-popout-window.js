/* Runs inside a pop-out panel window. It asks the editor that opened it for its panel, and asks
   again whenever it has none (after this window or the editor reloads). It closes itself once
   the editor window is gone, so no stale copy of the panel is left behind. */

const panel = new URLSearchParams(location.search).get('panel');

function connect() {
  let editor;
  try {
    editor = opener && !opener.closed ? opener : null;
    if (editor && !('document' in editor)) editor = null;
  } catch { editor = null; } // The editor tab moved to another site.
  if (!editor) { close(); return; }
  if (document.querySelector('.widget')) return;
  const result = editor.panelPopout?.attach(panel, window);
  if (result === 'busy') close(); // Another window already holds this panel.
}

connect();
setInterval(connect, 400);
