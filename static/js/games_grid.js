// Games Grid with Smart Scrollbar
class GamesGridManager {
  constructor(totalGames, gamesPerPage) {
    this.currentPage = 1;
    this.totalGames = totalGames;
    this.gamesPerPage = gamesPerPage;
    this.totalPages = Math.ceil(this.totalGames / this.gamesPerPage);
    this.isLoading = false;
    this.loadedPages = new Set([1]);
    
    this.gamesGrid = document.getElementById('gamesGrid');
    this.scrollbar = document.getElementById('smartScrollbar');
    this.scrollbarTrack = document.getElementById('scrollbarTrack');
    this.scrollbarThumb = document.getElementById('scrollbarThumb');
    this.scrollbarInfo = document.getElementById('scrollbarInfo');
    this.loadingIndicator = document.getElementById('loadingIndicator');
    
    this.init();
  }
  
  init() {
    this.setupScrollbar();
    this.setupInfiniteScroll();
    this.updateScrollbar();
  }
  
  setupScrollbar() {
    this.scrollbar.addEventListener('click', (e) => {
      if (this.isLoading) return;
      
      const rect = this.scrollbar.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const percentage = clickX / rect.width;
      const targetPage = Math.ceil(percentage * this.totalPages);
      this.jumpToPage(targetPage);
    });
    
    // Draggable thumb
    let isDragging = false;
    
    this.scrollbarThumb.addEventListener('mousedown', (e) => {
      isDragging = true;
      e.preventDefault();
    });
    
    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      
      const rect = this.scrollbar.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const percentage = Math.max(0, Math.min(1, mouseX / rect.width));
      const targetPage = Math.ceil(percentage * this.totalPages);
      this.jumpToPage(targetPage);
    });
    
    document.addEventListener('mouseup', () => {
      isDragging = false;
    });
  }
  
  setupInfiniteScroll() {
    // Load next page when scrolled near the right end
    this.gamesGrid.addEventListener('scroll', async () => {
      if (this.isLoading || this.currentPage >= this.totalPages) return;
      const nearRight = this.gamesGrid.scrollLeft + this.gamesGrid.clientWidth >= this.gamesGrid.scrollWidth - 200;
      if (nearRight) {
        await this.loadNextPage();
      }
    });
  }
  
  async jumpToPage(targetPage) {
    if (targetPage < 1 || targetPage > this.totalPages || this.isLoading) return;
    
    this.showLoading();
    
    // Load all pages from 1 to targetPage
    for (let page = 1; page <= targetPage; page++) {
      if (!this.loadedPages.has(page)) {
        await this.loadPage(page);
      }
    }
    
    this.currentPage = targetPage;
    this.updateScrollbar();
    this.hideLoading();
    
    // Scroll to appropriate position
    this.scrollToPage(targetPage);
  }
  
  async loadNextPage() {
    if (this.isLoading || this.currentPage >= this.totalPages) return;
    
    this.showLoading();
    await this.loadPage(this.currentPage + 1);
    this.currentPage++;
    this.updateScrollbar();
    this.hideLoading();
  }
  
  async loadPage(page) {
    if (this.loadedPages.has(page)) return;
    
    try {
      const response = await fetch(`?page=${page}`, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest'
        }
      });
      
      const data = await response.json();
      
      if (data.games_html) {
        // Append new games to the grid
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = data.games_html;
        
        while (tempDiv.firstChild) {
          this.gamesGrid.appendChild(tempDiv.firstChild);
        }
        
        this.loadedPages.add(page);
      }
    } catch (error) {
      console.error('Error loading games:', error);
    }
  }
  
  scrollToPage(page) {
    const cardWidth = 300 + 20; // card width + gap
    const targetIndex = (page - 1) * this.gamesPerPage;
    const targetScrollLeft = targetIndex * cardWidth;
    this.gamesGrid.scrollTo({ left: targetScrollLeft, behavior: 'smooth' });
  }
  
  updateScrollbar() {
    const percentage = this.currentPage / this.totalPages;
    this.scrollbarTrack.style.width = `${percentage * 100}%`;
    
    const loadedGames = this.currentPage * this.gamesPerPage;
    const actualLoadedGames = Math.min(loadedGames, this.totalGames);
    
    this.scrollbarInfo.textContent = `Показано ${actualLoadedGames} из ${this.totalGames} игр`;
  }
  
  showLoading() {
    this.isLoading = true;
    this.loadingIndicator.style.display = 'block';
  }
  
  hideLoading() {
    this.isLoading = false;
    this.loadingIndicator.style.display = 'none';
  }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // Get values from Django template variables
  const totalGames = parseInt(document.getElementById('gamesGrid').dataset.totalGames || '0');
  const gamesPerPage = parseInt(document.getElementById('gamesGrid').dataset.gamesPerPage || '20');
  
  if (totalGames > 0) {
    new GamesGridManager(totalGames, gamesPerPage);
  }
}); 