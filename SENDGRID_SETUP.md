# 📧 SendGrid Email Setup Guide

## Step-by-Step Setup (5 minutes)

### **Step 1: Create SendGrid Account**

1. Go to: **https://sendgrid.com/**
2. Click **"Start for Free"**
3. Sign up with your email
4. **Free Tier:** 100 emails/day (perfect for testing!)

---

### **Step 2: Verify Your Sender Email**

SendGrid requires sender verification to prevent spam.

#### **Option A: Single Sender Verification (Fastest - Recommended for Testing)**

1. In SendGrid Dashboard, go to: **Settings → Sender Authentication**
2. Click **"Verify a Single Sender"**
3. Fill in:
   - **From Name:** EduCare Bots
   - **From Email Address:** your-email@gmail.com (or any email you control)
   - **Reply To:** (same email)
   - **Company/Organization:** Your School Name
   - **Address, City, State, Zip:** (required fields)
4. Click **"Create"**
5. **Check your email** → Click verification link
6. ✅ Your sender email is now verified!

#### **Option B: Domain Authentication (For Production)**

Only needed if deploying to production with custom domain.

1. Go to: **Settings → Sender Authentication → Authenticate Your Domain**
2. Follow DNS configuration steps
3. (Skip this for testing)

---

### **Step 3: Generate API Key**

1. In SendGrid Dashboard, go to: **Settings → API Keys**
2. Click **"Create API Key"**
3. Name: `EduCare Bots`
4. Permissions: **Full Access** (or just "Mail Send")
5. Click **"Create & View"**
6. **COPY THE API KEY** (you won't see it again!)
   - It looks like: `SG.xxxxxxxxxxxxxxxxxxxxxx.yyyyyyyyyyyyyyyyyyyy`

---

### **Step 4: Update .env File**

Open `.env` and update:

```bash
# === Email Notifications (Kanban System - SendGrid) ===
SENDGRID_API_KEY=SG.your-actual-api-key-here
EMAIL_FROM=your-verified-email@gmail.com
EMAIL_FROM_NAME=EduCare Bots

# === Frontend URL (for email links) ===
FRONTEND_URL=http://localhost:3000
```

**Example:**
```bash
SENDGRID_API_KEY=SG.abc123xyz789.def456uvw012
EMAIL_FROM=noreply@yourschool.org
EMAIL_FROM_NAME=EduCare Bots
```

⚠️ **Important:**
- `EMAIL_FROM` MUST match the verified sender email from Step 2
- Don't share your API key!

---

### **Step 5: Install SendGrid**

```bash
pip install sendgrid
```

Or add to requirements and install all:
```bash
pip install -r requirements.txt
```

---

### **Step 6: Test Email**

1. **Update test script:**

Open `test_sendgrid_email.py` and change:
```python
to_email="your-actual-email@gmail.com",  # Change to your email!
```

2. **Run test:**
```bash
python test_sendgrid_email.py
```

3. **Check output:**
```
✅ Sent to your-email@gmail.com: Principal John invited you to 'Math Department'
✅ Success
```

4. **Check your inbox!** You should receive 2 beautiful HTML emails.

---

## 🎯 Quick Test Checklist

- [ ] SendGrid account created
- [ ] Sender email verified
- [ ] API key generated and copied
- [ ] `.env` file updated with API key
- [ ] `.env` has correct `EMAIL_FROM` (verified sender)
- [ ] SendGrid installed (`pip install sendgrid`)
- [ ] Test script updated with your email
- [ ] Test script ran successfully
- [ ] Emails received in inbox

---

## 🐛 Troubleshooting

### **Error: "SendGrid library not installed"**
**Fix:**
```bash
pip install sendgrid
```

### **Error: "SENDGRID_API_KEY not configured"**
**Fix:** Check `.env` file has:
```bash
SENDGRID_API_KEY=SG.your-key-here
```

### **Error: "EMAIL_FROM not configured"**
**Fix:** Add to `.env`:
```bash
EMAIL_FROM=your-verified-email@gmail.com
```

### **Error: "The from email does not match a verified Sender Identity"**
**Fix:**
1. Go to SendGrid → Settings → Sender Authentication
2. Verify the email you're using in `EMAIL_FROM`
3. Check email for verification link

### **Emails not arriving?**
1. Check **spam folder**
2. Check SendGrid dashboard: **Activity** tab
3. Look for bounce/delivery errors

### **Status 401 Unauthorized**
**Fix:** API key is wrong. Generate a new one.

### **Status 403 Forbidden**
**Fix:** API key doesn't have "Mail Send" permission. Create new key with Full Access.

---

## 📊 SendGrid Dashboard

After sending emails, check:
- **Dashboard → Activity:** See all sent emails
- **Dashboard → Stats:** Email delivery statistics
- **Dashboard → Suppressions:** Bounced/blocked emails

---

## 🚀 Production Tips

### **For Production Deployment:**

1. **Use Domain Authentication** (not Single Sender)
   - Better deliverability
   - No "via sendgrid.net" in emails

2. **Use a dedicated sending domain**
   - e.g., `noreply@mail.yourschool.com`
   - Not your main school email

3. **Enable Click/Open Tracking** (optional)
   - Settings → Tracking

4. **Set up webhooks** (optional)
   - Get notified of bounces, opens, clicks

5. **Monitor your quota**
   - Free: 100/day
   - Essentials: $19.95/month for 50k emails

---

## 💰 SendGrid Pricing

| Plan | Emails/Month | Price | Best For |
|------|--------------|-------|----------|
| **Free** | 100/day (3,000/month) | $0 | Testing, small schools |
| **Essentials** | 50,000/month | $19.95 | Small-medium schools |
| **Pro** | 1,500,000/month | $89.95 | Large schools/districts |

**For this project:** Free tier is perfect for testing and small deployments!

---

## 🔗 Useful Links

- SendGrid Dashboard: https://app.sendgrid.com/
- API Keys: https://app.sendgrid.com/settings/api_keys
- Sender Authentication: https://app.sendgrid.com/settings/sender_auth
- Activity Feed: https://app.sendgrid.com/email_activity
- Documentation: https://docs.sendgrid.com/

---

## 📧 Example Emails You'll Send

1. **Workspace Invitation**
   - Beautiful gradient header
   - Workspace details
   - Call-to-action button

2. **Task Assignment**
   - Priority badge (color-coded)
   - Due date
   - Task description
   - Direct link to task

3. **Comment Notification** (when implemented)
   - Comment preview
   - Link to discussion

All emails are professionally designed with HTML templates!

---

**Ready to test?** Run `python test_sendgrid_email.py` 🚀
