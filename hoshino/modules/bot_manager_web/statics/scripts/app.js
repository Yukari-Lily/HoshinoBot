/**
 * HoshinoBot Web Manager - Flat UI Engine
 * Lightweight, zero-dependency Vanilla JS
 */

// Toast Notifications System
function showToast(message, type = 'success', duration = 2500) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  let iconSvg = '';
  if (type === 'success') {
    iconSvg = '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"></path></svg>';
  } else if (type === 'error') {
    iconSvg = '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';
  } else {
    iconSvg = '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
  }

  toast.innerHTML = `${iconSvg}<span>${message}</span>`;
  container.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.add('show');
  });

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 200);
  }, duration);
}

// Mobile sidebar drawer
function setupMobileSidebar() {
  const toggleBtn = document.querySelector('.menu-toggle');
  const overlay = document.querySelector('.sidebar-overlay');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      document.body.classList.toggle('sidebar-open');
    });
  }
  if (overlay) {
    overlay.addEventListener('click', () => {
      document.body.classList.remove('sidebar-open');
    });
  }

  // Active navigation highlight
  const currentPath = window.location.pathname;
  document.querySelectorAll('.app-sidebar .nav-link').forEach(link => {
    const href = link.getAttribute('href');
    if (href) {
      if (href === currentPath || (href.length > 8 && currentPath.includes(href))) {
        link.classList.add('active');
      }
    }
  });
}

// Copy to clipboard helper
function copyText(text, successMsg = '已复制') {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(successMsg, 'success');
    }).catch(() => {
      fallbackCopy(text, successMsg);
    });
  } else {
    fallbackCopy(text, successMsg);
  }
}

function fallbackCopy(text, successMsg) {
  const input = document.createElement('input');
  input.value = text;
  document.body.appendChild(input);
  input.select();
  try {
    document.execCommand('copy');
    showToast(successMsg, 'success');
  } catch (err) {
    showToast('复制失败', 'error');
  }
  document.body.removeChild(input);
}

// Flat Filter Engine
class FlatFilterEngine {
  constructor({
    searchInputId = 'search-input',
    cardSelector = '.flat-item-card',
    filterChipSelector = '.segmented-btn',
    countBadgeId = 'item-count-badge',
    emptyStateId = 'empty-state-wrap',
    bundleAttr = 'data-bundle',
    searchFieldSelector = '.search-target'
  }) {
    this.searchInput = document.getElementById(searchInputId);
    this.cards = Array.from(document.querySelectorAll(cardSelector));
    this.chips = Array.from(document.querySelectorAll(filterChipSelector));
    this.countBadge = document.getElementById(countBadgeId);
    this.emptyState = document.getElementById(emptyStateId);
    this.bundleAttr = bundleAttr;
    this.searchFieldSelector = searchFieldSelector;
    this.currentBundle = 'ALL';
    this.searchKeyword = '';

    this.init();
  }

  init() {
    if (this.searchInput) {
      this.searchInput.addEventListener('input', (e) => {
        this.searchKeyword = e.target.value.trim().toLowerCase();
        this.applyFilter();
      });
    }

    if (this.chips.length > 0) {
      this.chips.forEach(chip => {
        chip.addEventListener('click', () => {
          this.chips.forEach(c => c.classList.remove('active'));
          chip.classList.add('active');
          this.currentBundle = chip.getAttribute('data-bundle') || 'ALL';
          this.applyFilter();
        });
      });
    }

    this.updateCount(this.cards.length);
  }

  applyFilter() {
    let visibleCount = 0;

    this.cards.forEach(card => {
      const cardBundle = card.getAttribute(this.bundleAttr) || '';
      const textElements = card.querySelectorAll(this.searchFieldSelector);
      let textContent = '';
      textElements.forEach(el => {
        textContent += ' ' + el.textContent.toLowerCase();
      });

      const matchesBundle = (this.currentBundle === 'ALL') || (cardBundle === this.currentBundle);
      const matchesSearch = !this.searchKeyword || textContent.includes(this.searchKeyword);

      if (matchesBundle && matchesSearch) {
        card.style.display = '';
        visibleCount++;
      } else {
        card.style.display = 'none';
      }
    });

    this.updateCount(visibleCount);

    if (this.emptyState) {
      this.emptyState.style.display = (visibleCount === 0) ? 'block' : 'none';
    }
  }

  updateCount(count) {
    if (this.countBadge) {
      this.countBadge.textContent = count;
    }
  }

  getVisibleCards() {
    return this.cards.filter(c => c.style.display !== 'none');
  }
}

// Flat Switch and State Manager
class FlatSwitchManager {
  constructor({
    conf,
    targetKey,
    mode,
    saveUrl,
    topSaveBtnId = 'btn-save-top',
    floatingSaveBtnId = 'btn-save-floating',
    floatingBannerId = 'floating-save-banner',
    progressFillId = 'progress-fill',
    progressTextId = 'progress-text',
    filterEngine
  }) {
    this.conf = JSON.parse(JSON.stringify(conf));
    this.originalConf = JSON.parse(JSON.stringify(conf));
    this.targetKey = targetKey;
    this.mode = mode;
    this.saveUrl = saveUrl;
    this.topSaveBtn = document.getElementById(topSaveBtnId);
    this.floatingSaveBtn = document.getElementById(floatingSaveBtnId);
    this.floatingBanner = document.getElementById(floatingBannerId);
    this.progressFill = document.getElementById(progressFillId);
    this.progressText = document.getElementById(progressTextId);
    this.filterEngine = filterEngine;
    this.isSaving = false;

    this.init();
  }

  init() {
    document.querySelectorAll('.flat-switch input').forEach(input => {
      input.addEventListener('change', (e) => {
        const itemKey = e.target.getAttribute('data-item-key');
        const checked = e.target.checked;
        this.updateItemState(itemKey, checked);
      });
    });

    if (this.topSaveBtn) {
      this.topSaveBtn.addEventListener('click', () => this.save());
    }
    if (this.floatingSaveBtn) {
      this.floatingSaveBtn.addEventListener('click', () => this.save());
    }

    const resetBtn = document.getElementById('btn-discard-floating');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => this.discardChanges());
    }

    this.updateProgressAndDirty();
  }

  updateItemState(itemKey, isEnabled) {
    if (this.mode === 'group_services') {
      this.conf[this.targetKey][itemKey] = isEnabled;
    } else {
      if (!this.conf[itemKey]) {
        this.conf[itemKey] = {};
      }
      this.conf[itemKey][this.targetKey] = isEnabled;
    }

    // Update label text and indicator dot
    const label = document.getElementById(`label-${itemKey}`);
    const dot = document.getElementById(`dot-${itemKey}`);
    if (label) {
      label.textContent = isEnabled ? '开启' : '关闭';
      label.style.color = isEnabled ? 'var(--success-text)' : 'var(--text-tertiary)';
    }
    if (dot) {
      dot.style.backgroundColor = isEnabled ? 'var(--success)' : 'var(--border-strong)';
    }

    // Update dirty status on card
    const card = document.getElementById(`card-${itemKey}`);
    if (card) {
      card.classList.toggle('is-dirty', this.checkItemDirty(itemKey));
    }

    this.updateProgressAndDirty();
  }

  checkItemDirty(itemKey) {
    if (this.mode === 'group_services') {
      return this.conf[this.targetKey][itemKey] !== this.originalConf[this.targetKey][itemKey];
    } else {
      const orig = this.originalConf[itemKey] ? this.originalConf[itemKey][this.targetKey] : false;
      const curr = this.conf[itemKey] ? this.conf[itemKey][this.targetKey] : false;
      return orig !== curr;
    }
  }

  getDirtyCount() {
    let dirty = 0;
    if (this.mode === 'group_services') {
      for (const k in this.conf[this.targetKey]) {
        if (this.conf[this.targetKey][k] !== this.originalConf[this.targetKey][k]) {
          dirty++;
        }
      }
    } else {
      for (const gid in this.conf) {
        const orig = this.originalConf[gid] ? this.originalConf[gid][this.targetKey] : false;
        const curr = this.conf[gid] ? this.conf[gid][this.targetKey] : false;
        if (orig !== curr) {
          dirty++;
        }
      }
    }
    return dirty;
  }

  getEnabledCount() {
    let enabled = 0;
    let total = 0;
    if (this.mode === 'group_services') {
      for (const k in this.conf[this.targetKey]) {
        total++;
        if (this.conf[this.targetKey][k]) enabled++;
      }
    } else {
      for (const gid in this.conf) {
        total++;
        if (this.conf[gid] && this.conf[gid][this.targetKey]) enabled++;
      }
    }
    return { enabled, total };
  }

  updateProgressAndDirty() {
    const { enabled, total } = this.getEnabledCount();
    const percent = total > 0 ? Math.round((enabled / total) * 100) : 0;

    if (this.progressFill) {
      this.progressFill.style.width = `${percent}%`;
    }
    if (this.progressText) {
      this.progressText.textContent = `${enabled} / ${total} (${percent}%)`;
    }

    const dirtyCount = this.getDirtyCount();

    // Update Floating Save Banner
    if (this.floatingBanner) {
      const dirtyBadge = document.getElementById('dirty-count-badge');
      if (dirtyBadge) dirtyBadge.textContent = dirtyCount;
      if (dirtyCount > 0) {
        this.floatingBanner.classList.add('visible');
      } else {
        this.floatingBanner.classList.remove('visible');
      }
    }

    // Update Top Save Button
    if (this.topSaveBtn) {
      if (dirtyCount > 0) {
        this.topSaveBtn.innerHTML = `保存修改 (${dirtyCount})`;
        this.topSaveBtn.className = 'btn btn-accent btn-sm';
      } else {
        this.topSaveBtn.innerHTML = `已是最新`;
        this.topSaveBtn.className = 'btn btn-secondary btn-sm';
      }
    }
  }

  discardChanges() {
    this.conf = JSON.parse(JSON.stringify(this.originalConf));

    document.querySelectorAll('.flat-switch input').forEach(input => {
      const itemKey = input.getAttribute('data-item-key');
      let origVal = false;
      if (this.mode === 'group_services') {
        origVal = this.originalConf[this.targetKey][itemKey];
      } else {
        origVal = this.originalConf[itemKey] ? this.originalConf[itemKey][this.targetKey] : false;
      }
      input.checked = origVal;

      const label = document.getElementById(`label-${itemKey}`);
      const dot = document.getElementById(`dot-${itemKey}`);
      if (label) {
        label.textContent = origVal ? '开启' : '关闭';
        label.style.color = origVal ? 'var(--success-text)' : 'var(--text-tertiary)';
      }
      if (dot) {
        dot.style.backgroundColor = origVal ? 'var(--success)' : 'var(--border-strong)';
      }

      const card = document.getElementById(`card-${itemKey}`);
      if (card) card.classList.remove('is-dirty');
    });

    this.updateProgressAndDirty();
    showToast('已放弃所有未保存修改', 'info');
  }

  setAllVisible(enable) {
    const visibleCards = this.filterEngine ? this.filterEngine.getVisibleCards() : Array.from(document.querySelectorAll('.flat-item-card'));
    let changed = 0;

    visibleCards.forEach(card => {
      const input = card.querySelector('.flat-switch input');
      if (input && input.checked !== enable) {
        input.checked = enable;
        const itemKey = input.getAttribute('data-item-key');
        this.updateItemState(itemKey, enable);
        changed++;
      }
    });

    showToast(`已${enable ? '开启' : '关闭'}当前 ${visibleCards.length} 项 (修改 ${changed} 项)`, 'info');
  }

  save() {
    if (this.isSaving) return;
    const dirtyCount = this.getDirtyCount();
    if (dirtyCount === 0) {
      showToast('配置未作任何更改', 'info');
      return;
    }

    this.isSaving = true;
    if (this.topSaveBtn) this.topSaveBtn.textContent = '保存中...';
    if (this.floatingSaveBtn) this.floatingSaveBtn.textContent = '保存中...';

    fetch(this.saveUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(this.conf)
    })
      .then(res => {
        if (!res.ok) throw new Error('网络请求错误');
        return res.json().catch(() => ({ code: 0, message: '保存成功' }));
      })
      .then(data => {
        this.originalConf = JSON.parse(JSON.stringify(this.conf));
        document.querySelectorAll('.flat-item-card.is-dirty').forEach(c => c.classList.remove('is-dirty'));
        this.updateProgressAndDirty();
        showToast('配置已保存生效', 'success');
      })
      .catch(err => {
        showToast('保存失败，请检查网络或服务', 'error');
      })
      .finally(() => {
        this.isSaving = false;
        if (this.floatingSaveBtn) this.floatingSaveBtn.textContent = '保存修改';
        this.updateProgressAndDirty();
      });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setupMobileSidebar();
});
