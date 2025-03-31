# LiveKit Voice Agent with Conversation Logging

This application creates a voice agent using LiveKit and OpenAI that can have spoken conversations with users. All conversations are automatically logged to both a local file and a Google Spreadsheet for review and analysis.

## Features

- Real-time voice conversations using LiveKit and OpenAI
- Speech-to-text and text-to-speech capabilities
- Automatic logging of all conversations with timestamps
- Export of conversation data to Google Sheets
- Graceful handling of application termination to ensure data is saved

## Prerequisites

- Python 3.9 or higher
- A LiveKit account and server
- An OpenAI API key with access to voice models
- A Google Cloud account with the Google Sheets API enabled
- A Google service account with access to Google Sheets

## Installation

1. Clone this repository
2. Install the required dependencies:

```bash
pip install -r requirements.txt
```

The requirements.txt file should include:

```
livekit-agents
aiofiles
python-dotenv
gspread
oauth2client
```

## Configuration

### 1. Environment Variables

Create a `.env.local` file in the root directory with the following variables:

```
LIVEKIT_URL=your_livekit_server_url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
OPENAI_API_KEY=your_openai_api_key
```

### 2. Google Sheets Credentials

To set up Google Sheets integration:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Google Sheets API:
   - Navigate to "APIs & Services" > "Library"
   - Search for "Google Sheets API" and enable it
4. Create a service account:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "Service Account"
   - Fill in the service account details and click "Create"
   - Grant the service account the "Editor" role for access to Google Sheets
5. Create and download the service account key:
   - In the service account list, click on your new service account
   - Go to the "Keys" tab
   - Click "Add Key" > "Create new key"
   - Choose JSON format and click "Create"
   - Save the downloaded JSON file as `credentials.json` in the root directory of this project

### 3. Google Spreadsheet Setup

1. Create a new Google Spreadsheet
2. Share the spreadsheet with the email address of your service account (it can be found in the credentials.json file under `client_email`)
3. Copy the spreadsheet ID from the URL (the long string after `/d/` and before `/edit`)
4. Update the `spreadsheet_id` parameter in the code with your spreadsheet ID

## Usage

Run the application with:

```bash
python agent.py dev
```

## How It Works

1. The application starts a LiveKit agent with OpenAI's voice capabilities
2. When a user joins the room, the agent begins listening for speech
3. User speech is transcribed and sent to the OpenAI model
4. The model's response is converted to speech and played back to the user
5. All interactions, including timestamps, transcriptions, and events are logged
6. When the application exits, all data is exported to a Google Spreadsheet

## Customizing the Agent

You can customize the agent's behavior by modifying:

- The OpenAI voice used (in the `voice` parameter)
- Temperature for response variation (higher = more creative/random)
- Instructions given to the agent
- Voice activity detection settings

## Troubleshooting

- **Google Sheets Auth Issues**: Make sure the service account has been given access to the spreadsheet
- **LiveKit Connection Problems**: Verify your LiveKit URL, API key, and secret are correct
- **Missing Transcriptions**: Adjust the voice activity detection settings if the agent is missing user speech

## License

This project is licensed under the MIT License - see the LICENSE file for details.