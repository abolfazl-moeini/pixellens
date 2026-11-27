# طرح توسعه **PixelLens** (ارتقای پروژه `iconkat`)

این طرح برای ارتقای پروژه [`iconkat`](file:///Users/moeini/Dev/iconkat) با نام جدید **PixelLens** تدوین شده است؛ به گونه‌ای که **تمام قابلیت‌ها، دستورات و رفتارهای قبلی `iconkat` ۱۰۰٪ بدون تغییر و سالم باقی بمانند** و قابلیت مقایسه پیکسلی، چیدمانی و ادراکی (`compare`) با الهام از `web_pixels_compare` در `craft-anything` به آن اضافه شود.

---

## ۱. هویت جدید و تضمین سازگاری عقب‌رو (Backward Compatibility)

* **نام جدید پروژه:** **PixelLens** (`pixellens`)
* **اصل عدم شکست رفتار قبلی:**
  1. فایل `iconkat.py` و تمام زیرماژول‌های فعلی (`json2svg/`, `gif2svg/`, `img2svg/`) دست‌نخورده باقی می‌مانند.
  2. اجرای مستقیم `python3 iconkat.py <input> [output] [flags]` دقیقاً مانند قبل کار می‌کند (تشخیص خودکار Lottie، GIF/APNG/WebP، آیکون ۲بعدی، تصویر ۳بعدی و تبدیل به SVG/AVIF/WebP).
  3. هیچ یک از فلگ‌های قبلی (`--inspect-only`, `--svg`, `--svg-only`, `--svg-engine`, `--wf-shapes`, `--wf-steps`, `--windfoil-dir`, `--size`, `--grid`, `--gif-mode`, `--step`) تغییر معنا نمی‌دهند.
  4. برای نام جدید، یک نقطه ورود `pixellens.py` (یا symlink/wrapper) در کنار `iconkat.py` اضافه می‌شود تا هر دو نام بدون تداخل کار کنند.

---

## ۲. اهداف فنی

1. **خودارزیابی تبدیل‌های PixelLens:** پس از تبدیل `PNG/GIF` به `SVG` (با `VTracer` یا `Windfoil`) یا `AVIF/WebP`، خروجی را دوباره رستر کرده و درصد شباهت به تصویر اصلی را بسنجد.
2. **سرویس‌دهی به `asf-v6` بدون افزودن وابستگی به کارخانه:** `asf-v6` بدون نیاز به اضافه کردن کتابخانه‌های پردازش تصویر به محیط پایتون خود یا بیلد Rust، با یک `subprocess` ساده خروجی دقیق JSON دریافت کند.
3. **بدون نیاز به نصب پکیج خارجی پایتون:** با توجه به اینکه روی محیط فعلی `Pillow` و `numpy` نصب نیستند و `ImageMagick` (`/opt/homebrew/bin/magick`) از قبل جزو پیش‌نیازهای پروژه است، پیاده‌سازی با **Python خالص (stdlib) + `magick`** انجام می‌شود تا هیچ وابستگی `pip` جدیدی تحمیل نشود.

---

## ۳. معماری و ساختار ماژول جدید

```text
iconkat/ (PixelLens)
├── iconkat.py               # حفظ کامل رفتار فعلی + افزودن زیرفرمان `compare` و فلگ `--verify`
├── pixellens.py             # نقطه ورود هم‌نام جدید (فراخوانی همان هسته بدون تکرار کد)
├── compare/
│   ├── __init__.py
│   └── pixels.py            # منطق خالص pixel_diff، layout_similarity، perceptual_similarity (SSIM) و noise_mask
├── tests/
│   └── test_compare.py      # تست‌های واحد با تصاویر ساختگی + تست رگرسیون رفتار قبلی iconkat
└── PLAN.md
```

### الگوریتم‌های `compare/pixels.py` (مبتنی بر منطق `web_common.rs`):
1. **Pixel Diff (`pixel_diff`):**
   * مقایسه پیکسل‌به‌پیکسل RGBA با آستانه `tolerance` (`0..=255`).
   * خروجی: `comparable`, `identical`, `differing_pixels`, `total_pixels`, `difference_fraction`, `max_channel_delta`, `changed_bounding_box`.
2. **ناحیه مشترک (`common_area`):**
   * وقتی ارتفاع یا عرض دو تصویر (مثلاً دو اسکرین‌شات وب) یکسان نیست، برش ناحیه مشترک از بالا-چپ (`min(w1, w2) × min(h1, h2)`).
3. **شباهت چیدمان (`layout_similarity`):**
   * تقسیم تصویر به کاشی‌های ۱۶×۱۶ پیکسل و محاسبه نسبت کاشی‌هایی که میانگین RGB آن‌ها در بازه مجاز (`threshold=24`) مطابقت دارد (`0.0` تا `1.0`).
4. **شباهت ادراکی (`perceptual_similarity`):**
   * کوچک‌سازی متناسب تصویر طوری که بزرگ‌ترین ضلع حداکثر `512px` باشد، سپس محاسبه میانگین SSIM روشنایی (Luminance) روی پنجره‌های `8×8` برای سنجش لبه‌ها، متن و بافت (`0.0` تا `1.0`).
5. **ماسک نواحی پویا (`noise_mask` / `--ignore`):**
   * دریافت تصویر ماسک (`noise-<width>.png`) و نادیده گرفتن کاشی‌های سفید (انیمیشن، اسلایدر، ویدیو) در امتیازدهی.
6. **تصویر اختلاف (`--diff-out`):**
   * تولید اختیاری یک PNG که پیکسل‌های دارای اختلاف را با رنگ قرمز روی پس‌زمینه خاکستری تیره نمایش دهد.

---

## ۴. رابط CLI و قرارداد خروجی JSON

### الف) زیرفرمان `compare` (مخصوص `asf-v6` و تست دستی)
```bash
python3 pixellens.py compare before.png after.png \
  --common-area \
  --tolerance 8 \
  --ignore noise-mask.png \
  --diff-out diff.png \
  --min-similarity 0.90 \
  --json
```
*(نکته: `python3 iconkat.py compare ...` نیز دقیقاً همین کار را انجام می‌دهد و هیچ تداخلی با حالت عادی `<input>` ندارد، چون فقط وقتی آرگومان اول دقیقاً `compare` باشد وارد این مسیر می‌شود.)*

**خروجی استاندارد JSON:**
```json
{
  "comparable": true,
  "identical": false,
  "sizes": {
    "before": {"width": 1440, "height": 900},
    "after": {"width": 1440, "height": 940}
  },
  "shared_area": {"width": 1440, "height": 900},
  "total_pixels": 1296000,
  "differing_pixels": 14200,
  "max_channel_delta": 182,
  "layout_similarity": 0.94,
  "perceptual_similarity": 0.91,
  "ignored_tiles": 12,
  "passed": true
}
```

### ب) فلگ `--verify` در تبدیل‌های PixelLens / iconkat
```bash
python3 pixellens.py icon3d.png --svg --svg-engine windfoil --verify
```
* خروجی SVG/AVIF/WebP ساخته‌شده با `magick` به PNG هم‌سایز ورودی رستر می‌شود و امتیاز `layout_similarity` و `perceptual_similarity` چاپ می‌شود.

---

## ۵. مراحل اجرا (پس از تایید شما)

1. **فاز ۱ — تست‌های رگرسیون و واحد (TDD Red):**
   * نوشتن `tests/test_compare.py` شامل تست‌های رفتار فعلی `iconkat.py` (تا مطمئن شویم هیچ چیز قبلی خراب نمی‌شود) و تست‌های مقایسه پیکسلی/SSIM/ماسک.
2. **فاز ۲ — هسته مقایسه (`compare/pixels.py`):**
   * پیاده‌سازی با Python stdlib + `magick` بدون نیاز به هیچ پکیج `pip` اضافی.
3. **فاز ۳ — اتصال ایمن به CLI (`iconkat.py` و `pixellens.py`):**
   * افزودن زیرفرمان `compare`، فلگ `--verify` و نقطه ورود `pixellens.py` با حفظ ۱۰۰٪ سازگاری عقب‌رو.
4. **فاز ۴ — تایید نهایی:**
   * اجرای `pytest` روی کل تست‌ها و تست دستی تبدیل‌های قبلی (`--inspect-only`, `--svg`, `--grid`) روی فایل‌های نمونه.
