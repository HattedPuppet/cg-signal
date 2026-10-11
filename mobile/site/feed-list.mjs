/** Own the feed's rendered cards, incremental range, and viewport anchor. */
export class FeedList {
  constructor({ list, moreButton, stickyHeader, renderCard, updateCard }) {
    this.list = list;
    this.moreButton = moreButton;
    this.stickyHeader = stickyHeader;
    this.renderCard = renderCard;
    this.updateCard = updateCard;
    this.cards = new Map();
    this.articles = [];
    this.context = null;
    this.limit = 40;
    this.moreButton.addEventListener("click", () => this.loadMore({ focusNew: true }));
    if ("IntersectionObserver" in window) {
      this.observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) this.loadMore();
      }, { rootMargin: "400px 0px" });
      this.observer.observe(this.moreButton);
    }
  }

  capturePosition() {
    const headerBottom = this.stickyHeader.getBoundingClientRect().bottom;
    const visible = [];
    for (const card of this.list.children) {
      const bounds = card.getBoundingClientRect();
      if (card.dataset.articleId && bounds.bottom > headerBottom + 24 && bounds.top < innerHeight - 24) {
        visible.push({ id: card.dataset.articleId, top: bounds.top });
      }
    }
    return visible.length ? { ...visible[0], alternatives: visible.slice(1) } : null;
  }

  restorePosition(position) {
    if (!position) return;
    position = [position, ...(position.alternatives || [])].find((candidate) => this.cards.has(candidate.id)) || position;
    const card = this.cards.get(position.id)?.node || this.list.firstElementChild;
    const headerBottom = this.stickyHeader.getBoundingClientRect().bottom;
    const bounds = (card || this.list).getBoundingClientRect();
    const targetTop = this.cards.has(position.id) && position.top + bounds.height > headerBottom
      ? position.top : headerBottom + 8;
    const currentTop = bounds.top;
    window.scrollBy({ top: currentTop - targetTop, behavior: "instant" });
  }

  render(articles, { context = this.context, position = this.capturePosition() } = {}) {
    if (context !== this.context) this.limit = 40;
    this.context = context;
    this.articles = articles;
    // Restoring a deep reading position needs enough preceding cards to reach
    // that anchor. Otherwise only the first batch is created, not the full feed.
    if (position) {
      const eligibleIds = new Set(articles.map((article) => article.id));
      position = [position, ...(position.alternatives || [])].find((candidate) => eligibleIds.has(candidate.id)) || position;
    }
    const anchorIndex = position ? articles.findIndex((article) => article.id === position.id) : -1;
    this.limit = Math.max(this.limit, anchorIndex + 1);
    const visible = articles.slice(0, this.limit);
    const ids = new Set(visible.map((article) => article.id));
    for (const [id, card] of this.cards) {
      if (!ids.has(id)) {
        card.node.remove();
        this.cards.delete(id);
      }
    }
    for (const skeleton of this.list.querySelectorAll(".skeleton")) skeleton.remove();

    let cursor = this.list.firstElementChild;
    for (const article of visible) {
      const signature = JSON.stringify(article);
      let card = this.cards.get(article.id);
      if (!card || card.signature !== signature) {
        const template = document.createElement("template");
        template.innerHTML = this.renderCard(article).trim();
        const node = template.content.firstElementChild;
        node.dataset.articleId = article.id;
        if (card) {
          if (cursor === card.node) cursor = node;
          card.node.replaceWith(node);
        }
        card = { node, signature };
        this.cards.set(article.id, card);
      }
      this.updateCard(card.node, article);
      if (card.node !== cursor) this.list.insertBefore(card.node, cursor);
      cursor = card.node.nextElementSibling;
    }

    const remaining = articles.length - visible.length;
    this.moreButton.hidden = remaining <= 0;
    this.moreButton.textContent = `Show ${Math.min(40, remaining)} more stories · ${visible.length} of ${articles.length}`;
    this.restorePosition(position);
  }

  resetRange() {
    this.context = null;
  }

  loadMore({ focusNew = false } = {}) {
    if (this.limit >= this.articles.length) return;
    const firstNewArticle = this.articles[this.limit];
    this.limit += 40;
    this.render(this.articles);
    if (focusNew) this.cards.get(firstNewArticle.id)?.node.querySelector("h3 a").focus();
  }
}
