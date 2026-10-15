import { createContext, useContext } from 'react'

export const STRINGS = {
  en: {
    app: 'Bookmaker', tagline: 'Illustrated children\'s books', dashboard: 'Dashboard', newBook: 'New book', library: 'Library', providers: 'Models & providers',
    running: 'In progress', failed: 'Failed', completed: 'Completed', queue: 'Queue', cost: 'API cost', resources: 'Resources', recent: 'Recent books',
    createQuick: 'Create a book', noBooks: 'No books yet. Create your first one!', status: 'Status', progress: 'Progress', open: 'Open',
    download: 'Download PDF', title: 'Title', titleHint: 'Leave empty to get a suggestion', topic: 'Topic / story idea', moral: 'Moral or learning goal',
    age: 'Age group', language: 'Book language', pages: 'Story pages', complexity: 'Complexity', narration: 'Narration', style: 'Illustration style',
    aspect: 'Image aspect ratio', pageSize: 'Page size', orientation: 'Orientation', mainChars: 'Main characters', heroName: 'Hero name (optional)',
    textAmount: 'Text per page', fantasy: 'Fantasy level', humor: 'Humour', ending: 'Ending', coverStyle: 'Cover style', author: 'Author', publisher: 'Publisher / brand',
    pageNumbers: 'Page numbers', backCover: 'Back cover', textInImage: 'Allow text inside images', profile: 'Output quality', seed: 'Seed (optional, for reproducibility)',
    llm: 'Story model', imageModel: 'Image model', vision: 'Vision reviewer (optional)', presets: 'Quick presets', estimate: 'Estimate', start: 'Generate book',
    saveDraft: 'Save as draft', minutes: 'min', images: 'images', paidWarn: 'This run uses paid APIs. Estimated cost:', confirm: 'I accept the estimated cost',
    pagesTab: 'Pages', charactersTab: 'Characters', previewTab: 'Preview', qaTab: 'Quality report', historyTab: 'History', logsTab: 'Logs', settingsTab: 'Settings',
    pause: 'Pause', resume: 'Resume', rebuild: 'Rebuild PDF', export: 'Backup (.zip)', archive: 'Archive', unarchive: 'Restore', delete: 'Delete',
    save: 'Save', regenImage: 'Regenerate image', regenText: 'Rewrite text', approve: 'Approve image', unlock: 'Unlock', moveUp: 'Up', moveDown: 'Down',
    addPage: 'Add page after', removePage: 'Delete page', imagePrompt: 'Illustration brief', text: 'Text', instruction: 'Instruction for rewrite (optional)',
    single: 'Single page', double: 'Two pages', zoom: 'Zoom', goTo: 'Go to page', warnings: 'Warnings', errors: 'Errors', noIssues: 'No issues found',
    story: 'Story', imagesSec: 'Images', layout: 'Layout', pdf: 'PDF', name: 'Name', description: 'Visual description', colors: 'Signature colours',
    uploadRef: 'Upload reference image', reference: 'Reference sheet', addProvider: 'Add provider', check: 'Test connection', defaults: 'Defaults',
    kind: 'Kind', type: 'Type', baseUrl: 'Base URL', model: 'Model', apiKey: 'API key', keepKey: 'leave empty to keep the stored key', fallback: 'Fallback provider',
    pricing: 'Pricing (USD)', rpm: 'Max requests / minute', workflow: 'ComfyUI workflow (API format JSON)', usage: 'Recent usage', confirmDelete: 'Type the book id to delete:',
    archived: 'Archived', all: 'All', search: 'Search…', settingsLocked: 'Some settings can be changed later and applied with “Rebuild PDF”.', token: 'API token',
    stage: 'Stage', none: 'none', cover: 'Cover', regenCover: 'Regenerate cover', locked: 'locked', edit: 'Edit', cancel: 'Cancel', close: 'Close',
    offlineNote: 'Built-in engines work without internet, GPU or API key. Connect Ollama, an OpenAI-compatible API, ComfyUI or Automatic1111 for AI generation.',
  },
  fa: {
    app: 'کتاب‌ساز', tagline: 'کتاب‌های داستان مصور کودک', dashboard: 'داشبورد', newBook: 'کتاب جدید', library: 'کتابخانه', providers: 'مدل‌ها و سرویس‌ها',
    running: 'در حال تولید', failed: 'ناموفق', completed: 'تکمیل‌شده', queue: 'صف', cost: 'هزینه‌ی API', resources: 'منابع', recent: 'کتاب‌های اخیر',
    createQuick: 'ساخت کتاب', noBooks: 'هنوز کتابی نساخته‌اید. اولین کتاب را بسازید!', status: 'وضعیت', progress: 'پیشرفت', open: 'باز کردن',
    download: 'دانلود PDF', title: 'عنوان', titleHint: 'برای پیشنهاد خودکار خالی بگذارید', topic: 'موضوع / ایده‌ی داستان', moral: 'پیام اخلاقی یا آموزشی',
    age: 'گروه سنی', language: 'زبان کتاب', pages: 'تعداد صفحات داستان', complexity: 'پیچیدگی', narration: 'راوی', style: 'سبک تصویرسازی',
    aspect: 'نسبت تصویر', pageSize: 'اندازه‌ی صفحه', orientation: 'جهت صفحه', mainChars: 'شخصیت‌های اصلی', heroName: 'نام قهرمان (اختیاری)',
    textAmount: 'میزان متن هر صفحه', fantasy: 'میزان تخیل', humor: 'طنز', ending: 'پایان‌بندی', coverStyle: 'سبک جلد', author: 'نویسنده', publisher: 'ناشر / برند',
    pageNumbers: 'شماره‌ی صفحه', backCover: 'پشت جلد', textInImage: 'اجازه‌ی نوشته در تصویر', profile: 'کیفیت خروجی', seed: 'Seed (اختیاری، برای تکرارپذیری)',
    llm: 'مدل داستان', imageModel: 'مدل تصویر', vision: 'بازبین تصویری (اختیاری)', presets: 'تنظیمات آماده', estimate: 'برآورد', start: 'تولید کتاب',
    saveDraft: 'ذخیره به‌عنوان پیش‌نویس', minutes: 'دقیقه', images: 'تصویر', paidWarn: 'این اجرا از API پولی استفاده می‌کند. هزینه‌ی برآوردی:', confirm: 'هزینه‌ی برآوردی را می‌پذیرم',
    pagesTab: 'صفحات', charactersTab: 'شخصیت‌ها', previewTab: 'پیش‌نمایش', qaTab: 'گزارش کیفیت', historyTab: 'تاریخچه', logsTab: 'لاگ‌ها', settingsTab: 'تنظیمات',
    pause: 'توقف', resume: 'ادامه', rebuild: 'ساخت دوباره‌ی PDF', export: 'پشتیبان (.zip)', archive: 'بایگانی', unarchive: 'بازگردانی', delete: 'حذف',
    save: 'ذخیره', regenImage: 'تولید دوباره‌ی تصویر', regenText: 'بازنویسی متن', approve: 'تأیید تصویر', unlock: 'باز کردن قفل', moveUp: 'بالا', moveDown: 'پایین',
    addPage: 'افزودن صفحه بعد از این', removePage: 'حذف صفحه', imagePrompt: 'توضیح تصویر', text: 'متن', instruction: 'دستور بازنویسی (اختیاری)',
    single: 'تک‌صفحه', double: 'دوصفحه', zoom: 'بزرگ‌نمایی', goTo: 'رفتن به صفحه', warnings: 'هشدارها', errors: 'خطاها', noIssues: 'مشکلی پیدا نشد',
    story: 'داستان', imagesSec: 'تصاویر', layout: 'صفحه‌آرایی', pdf: 'PDF', name: 'نام', description: 'توضیح ظاهری', colors: 'رنگ‌های شاخص',
    uploadRef: 'بارگذاری تصویر مرجع', reference: 'برگه‌ی مرجع', addProvider: 'افزودن سرویس', check: 'بررسی اتصال', defaults: 'پیش‌فرض‌ها',
    kind: 'نوع کار', type: 'نوع سرویس', baseUrl: 'آدرس سرویس', model: 'مدل', apiKey: 'کلید API', keepKey: 'برای حفظ کلید فعلی خالی بگذارید', fallback: 'سرویس جایگزین',
    pricing: 'قیمت (دلار)', rpm: 'حداکثر درخواست در دقیقه', workflow: 'گردش‌کار ComfyUI (JSON با فرمت API)', usage: 'مصرف اخیر', confirmDelete: 'برای حذف، شناسه‌ی کتاب را وارد کنید:',
    archived: 'بایگانی‌شده', all: 'همه', search: 'جست‌وجو…', settingsLocked: 'برخی تنظیمات را می‌توان بعداً تغییر داد و با «ساخت دوباره‌ی PDF» اعمال کرد.', token: 'توکن API',
    stage: 'مرحله', none: 'هیچ', cover: 'جلد', regenCover: 'تولید دوباره‌ی جلد', locked: 'قفل', edit: 'ویرایش', cancel: 'انصراف', close: 'بستن',
    offlineNote: 'موتورهای داخلی بدون اینترنت، GPU و کلید API کار می‌کنند. برای تولید با هوش مصنوعی Ollama، یک API سازگار با OpenAI، ComfyUI یا Automatic1111 را متصل کنید.',
  },
}

export const LangContext = createContext({ lang: 'en', t: (k) => k })
export const useT = () => useContext(LangContext)
export const makeT = (lang) => (k) => (STRINGS[lang] && STRINGS[lang][k]) || STRINGS.en[k] || k

export const STATUS_COLORS = {
  completed: 'ok', completed_with_warnings: 'warn', needs_review: 'warn', failed: 'bad', running: 'run', queued: 'run', paused: 'muted',
  draft: 'muted', cancelled: 'muted',
}
