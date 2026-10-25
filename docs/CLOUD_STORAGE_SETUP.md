# Cloud Storage Configuration Guide

Because free cloud compute containers (like Render Free) use ephemeral storage where files are erased on restart, configuring external object storage ensures your Zoom recordings are permanently preserved and accessible.

---

## 🌟 Recommended Solution: Cloudflare R2 (100% Free 10GB Tier)

Cloudflare R2 provides an S3-compatible API with **10GB free storage every month** and **zero egress (download) fees**.

### Step 1: Create a Cloudflare Account & Bucket
1. Open your mobile browser or desktop and visit **[dash.cloudflare.com](https://dash.cloudflare.com/)**.
2. Sign in or create a free account.
3. In the sidebar menu, select **R2 Object Storage**.
4. Tap **Create Bucket**.
5. Name your bucket: `zoom-recordings`.
6. Select **Automatic** location and tap **Create Bucket**.

### Step 2: Generate S3 API Credentials
1. In the R2 Dashboard, tap **Manage R2 API Tokens** (on the right side).
2. Tap **Create API Token**.
3. Set Permissions to: **Admin Read & Write** (or Object Read & Write).
4. Tap **Create API Token**.
5. You will see:
   - **Access Key ID:** `abc123456789...`
   - **Secret Access Key:** `def987654321...`
   - **Jurisdiction-specific endpoint:** `https://<account_id>.r2.cloudflarestorage.com`

### Step 3: Configure Environment Variables
Add these values to your `.env` file or Render environment variables:

```ini
STORAGE_PROVIDER=s3
S3_ENDPOINT_URL=https://<your_account_id>.r2.cloudflarestorage.com
S3_BUCKET_NAME=zoom-recordings
S3_ACCESS_KEY_ID=<your_access_key_id>
S3_SECRET_ACCESS_KEY=<your_secret_access_key>
S3_REGION_NAME=auto
S3_URL_EXPIRATION_SECONDS=86400
CLEANUP_LOCAL_FILES_AFTER_UPLOAD=true
```

---

## Alternative: AWS S3 Free Tier

If you prefer Amazon Web Services:
1. Create a bucket in AWS S3 (e.g. `my-zoom-recordings`).
2. Create an IAM User with `AmazonS3FullAccess` and generate an Access Key.
3. Configure:
```ini
STORAGE_PROVIDER=s3
S3_ENDPOINT_URL=
S3_BUCKET_NAME=my-zoom-recordings
S3_ACCESS_KEY_ID=AKIA...
S3_SECRET_ACCESS_KEY=your_secret_key
S3_REGION_NAME=us-east-1
S3_URL_EXPIRATION_SECONDS=86400
CLEANUP_LOCAL_FILES_AFTER_UPLOAD=true
```

---

## Alternative: Local Storage (Development / Self-Hosting)

If running locally on a VPS, Raspberry Pi, or local computer:
```ini
STORAGE_PROVIDER=local
LOCAL_STORAGE_DIR=./recordings
CLEANUP_LOCAL_FILES_AFTER_UPLOAD=false
```
The recordings will remain permanently in your local `./recordings` directory.
