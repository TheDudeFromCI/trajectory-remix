/* Native disclosure menus keep the retained application shell keyboard usable. */
const menus = [...document.querySelectorAll('.app-menu')];
for (const menu of menus) {
  menu.addEventListener('toggle', () => {
    if (menu.open) for (const other of menus) if (other !== menu) other.open = false;
  });
  menu.addEventListener('click', event => {
    if (event.target.closest('button')) menu.open = false;
  });
}
document.addEventListener('click', event => {
  if (!event.target.closest('.app-menu')) for (const menu of menus) menu.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    const open = menus.find(menu => menu.open);
    if (open) { open.open = false; open.querySelector('summary').focus(); }
  }
});
for (const [id, cls] of [['toggle-dataset-panel', 'dataset-panel-hidden'], ['toggle-editor-panel', 'editor-panel-hidden']]) {
  document.getElementById(id)?.addEventListener('click', () => document.getElementById('workspace').classList.toggle(cls));
}
