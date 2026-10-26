/* Presentation-only shortcut affordance. Recording stays in the shared controller. */
(function () {
  'use strict';
  const shortcut = document.getElementById('shortcut');
  document.getElementById('shortcut-edit').onclick = () => {
    if (!shortcut.disabled) shortcut.focus();
  };
})();
