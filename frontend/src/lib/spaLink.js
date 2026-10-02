// In-app navigation without a full reload (same pattern as the extension link
// on the home page). Real <a href> stays so crawlers and middle-click work.
export function spaLink(path) {
  return (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
    e.preventDefault();
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
    window.scrollTo(0, 0);
  };
}
