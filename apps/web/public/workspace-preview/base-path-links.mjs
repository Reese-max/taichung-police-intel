function repairReturnLinks(root = document) {
  root.querySelectorAll('a[href="../../"]').forEach(link => link.setAttribute('href', '../'));
}

repairReturnLinks();
new MutationObserver(() => repairReturnLinks()).observe(document.body, { childList: true, subtree: true });
