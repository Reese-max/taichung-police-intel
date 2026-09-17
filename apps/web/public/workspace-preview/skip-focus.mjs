const skipLink = document.querySelector('.skip-link');

skipLink?.addEventListener('click', event => {
  event.preventDefault();
  const main = document.querySelector('#main-content');
  if (!main) return;
  main.focus({ preventScroll: true });
  main.scrollIntoView({ block: 'start', behavior: 'instant' });
});
