# ComfyUI Telegram Bot

A Telegram bot that generates images using ComfyUI workflows via RunPod API.

## Features

- 🎨 AI-powered image generation from text prompts
- 💳 Credit system with 2 free daily generations per user
- 📊 User tracking and generation history
- ⚡ Queue management for concurrent requests
- 🔄 Automatic seed randomization for unique outputs
- 📝 Database persistence for user data
- 🚀 Production-ready with webhook support

## Prerequisites

1. **Telegram Bot Token**: Create a bot via [@BotFather](https://t.me/botfather)
2. **RunPod Account**: Set up an endpoint with ComfyUI
3. **ComfyUI Workflow**: A compatible workflow JSON file
4. **Python 3.10+** or Docker

## Installation

### Option 1: Python Virtual Environment

```bash
# Clone or download the files
cd comfy-telegram-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Option 2: Docker

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials

# Build and run with Docker Compose
docker-compose up -d
```

## Configuration

Edit the `.env` file with your settings:

```env
# Required
TELEGRAM_KEY=your_bot_token_here
RUNPOD_ENDPOINT_ID=your_endpoint_id
RUNPOD_API_KEY=your_api_key

# Workflow path (must be accessible)
WORKFLOW_PATH=/path/to/your/workflow.json

# For production (optional)
WEBHOOK_URL=https://your-domain.com/webhook
PORT=8000
```

## Running the Bot

### Development Mode (Polling)

```bash
# Without webhook (uses polling)
python bot.py
```

### Production Mode (Webhook)

1. Set up webhook:
```bash
python webhook.py
```

2. Run the bot:
```bash
python bot.py
```

### Using Docker

```bash
# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

## Bot Commands

- `/start` - Welcome message and instructions
- `/generate <prompt>` - Generate an image
- `/balance` - Check credits and free usage
- `/examples` - Show prompt examples
- `/terms` - Display terms of service
- `/help` - List all commands

## API Endpoints

The bot exposes these HTTP endpoints:

- `GET /` - Health check and queue status
- `GET /stats` - Bot statistics
- `POST /webhook` - Telegram webhook endpoint

## Workflow Integration

The bot expects a ComfyUI workflow with:
- Node 45 containing a prompt input field (`string_a`)
- Seed/noise_seed inputs for randomization
- Output format that includes base64 encoded images

## Database Schema

The bot creates two SQLite tables:

### users
- `user_id` - Telegram user ID (primary key)
- `credits` - Paid generation credits
- `free_used` - Daily free generations used
- `last_reset` - Date of last free usage reset
- `total_generated` - Total images generated
- `created_at` - User registration timestamp

### generations
- `id` - Generation ID (auto-increment)
- `user_id` - User who requested
- `prompt` - Generation prompt
- `status` - Current status (queued/processing/completed/failed)
- `job_id` - RunPod job ID
- `created_at` - Request timestamp
- `completed_at` - Completion timestamp
- `error_message` - Error details if failed

## Customization

### Modify Generation Limits

In `bot.py`:
- Daily free generations: change the `free_used < 2` logic in the `/balance` and `/generate` handlers.
- Queue settings: adjust `job_queue = queue.Queue(maxsize=10)`.
- Polling settings: tweak `POLL_INTERVAL` and `MAX_POLL_TIME`.

### Add Credit Purchase

Implement a payment handler in the webhook function:

```python
elif text == "/buy":
    # Add your payment integration here
    # Update user credits after successful payment
    update_user(chat_id, credits=new_credit_amount)
```

### Custom Workflows

To use different workflows:
1. Update `WORKFLOW_PATH` in `.env`
2. Modify the prompt node ID in `update_prompt_in_workflow()` if needed
3. Adjust seed randomization nodes if different

## Monitoring

### Check Bot Status

```bash
# Health check
curl http://localhost:8000/

# Statistics
curl http://localhost:8000/stats
```

### View Logs

```bash
# If using Docker
docker-compose logs -f telegram-bot

# If running directly
# Logs are printed to console
```

### Webhook Status

```bash
# Check current webhook
python webhook.py info

# Remove webhook (for development)
python webhook.py delete
```

## Troubleshooting

### Bot not responding
1. Check bot token is correct
2. Verify webhook is set (production) or deleted (development)
3. Check logs for errors

### Generation failures
1. Verify RunPod endpoint is running
2. Check API key is valid
3. Ensure workflow file is accessible
4. Review RunPod logs for ComfyUI errors

### Database issues
1. Ensure write permissions for database directory
2. Check disk space
3. Delete `users.db` to reset (loses all data)

### Queue full errors
- Increase `QUEUE_MAX_SIZE` in code
- Or wait for current jobs to complete

## Security Considerations

1. **Never commit `.env` file** - Add to `.gitignore`
2. **Validate prompts** - Consider adding content filters
3. **Rate limiting** - Implemented via queue and daily limits
4. **Use HTTPS** for webhook in production
5. **Rotate API keys** regularly
6. **Monitor for abuse** - Check generation logs

## Production Deployment

### RunPod Deployment

1. Create a CPU pod on RunPod
2. Install Docker or Python environment
3. Upload bot files
4. Configure environment variables
5. Set up persistent storage for database
6. Configure webhook URL pointing to pod
7. Start the bot service

### Alternative Hosting

The bot can run on any platform that supports:
- Python or Docker
- Persistent storage for SQLite
- HTTPS endpoint for webhook
- Outbound HTTPS for APIs

## Support

For issues:
1. Check logs for detailed error messages
2. Verify all environment variables
3. Test with simple prompts first
4. Check RunPod endpoint status
5. Ensure workflow compatibility

## License

This bot is provided as-is for educational purposes. Ensure you comply with:
- Telegram Bot API terms
- RunPod terms of service
- ComfyUI licensing
- Content generation policies
