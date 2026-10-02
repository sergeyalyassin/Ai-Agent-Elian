# AI Personal Agent

وكيل شخصي عربي يعمل من Telegram أو Termux أو GitHub Actions. يعتمد على Python standard library وOpenRouter، ولا يخز�� مفاتيح API في الكود.

## ما هو منفذ الآن

- Telegram polling مع تقييد اختياري بـ `CHAT_ID`.
- نماذج OpenRouter مجانية مع fallback وcooldown وتفضيل النموذج الناجح لكل نوع مهمة.
- ذاكرة JSON دائمة للمحادثات والأخطاء والنجاحات والإحصاءات والمهام.
- بحث ويب، أخبار، ويكيبيديا، طقس، وظائف عن بعد، منح ودورات.
- بحث علمي في arXiv وPubMed وOpenAlex وSemantic Scholar وCrossref.
- Gmail عبر IMAP للقراءة فقط.
- مساعد البرمجة: `/code` و`/review` و`/learn`، مع دعم اللغة الطبيعية.
- مهام دورية، وpatches للتطوير الذاتي لا تطبق إلا بعد `/approve`.
- ذاكرة قابلة للبحث عبر `/remember` و`/recall`، عناصر محفوظة بلا تكرار عبر `/save` و`/saved`، وبطاقات وأهداف تعلم محلية.
- طابور موافقة صريح للعمليات الحساسة عبر `/request` ثم `/approve-action` أو `/reject-action`؛ لا ينفذ أي إجراء خارجي تلقائياً.

الوظائف التي تتطلب اعتماداً أو موافقة صريحة — مثل إرسال البريد، النشر، التقديم النهائي للوظائف أو تشغيل أوامر نظام خطرة — **ليست منفذة كعمليات تلقائية**.

## البدء السريع

```bash
cp .env.example .env
# اضبط المتغيرات في بيئتك، ولا تضع .env في Git.
export TELEGRAM_TOKEN='...'
export CHAT_ID='...'
export OPENROUTER_API_KEY='...'
python3 agent.py
