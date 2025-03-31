import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Union, List, Dict, Any
import atexit
import signal

import aiofiles
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    multimodal,
    utils,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.multimodal.multimodal_agent import EventTypes
from livekit.plugins import openai

# Load environment variables from .env.local file
load_dotenv(dotenv_path=".env.local")

@dataclass
class EventLog:
    """Tracks LiveKit agent events with timestamps"""
    eventname: str | None
    """name of recorded event"""
    time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    """time the event is recorded"""


@dataclass
class TranscriptionLog:
    """Stores speech transcriptions with speaker info and timestamps"""
    role: str | None
    """role of the speaker"""
    transcription: str | None
    """transcription of speech"""
    time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    """time the event is recorded"""

class ConversationPersistor(utils.EventEmitter[EventTypes]):
    def __init__(
        self,
        *,
        model: multimodal.MultimodalAgent | None,
        log: str | None,
        transcriptions_only: bool = False,
        sheets_credentials_path: str | None = None,
        spreadsheet_id: str | None = None,
    ):
        """
        Initializes a ConversationPersistor instance which records the events and transcriptions of a MultimodalAgent.

        Args:
            model (multimodal.MultimodalAgent): an instance of a MultiModalAgent
            log (str): name of the external file to record events in
            transcriptions_only (bool): a boolean variable to determine if only transcriptions will be recorded, False by default
            sheets_credentials_path (str): path to Google Sheets API credentials JSON file
            spreadsheet_id (str): ID of the Google Spreadsheet to write to
        """
        super().__init__()

        self._model = model
        self._log = log
        self._transcriptions_only = transcriptions_only
        self._sheets_credentials_path = sheets_credentials_path
        self._spreadsheet_id = spreadsheet_id
        self._gc = None  # Will hold Google Sheets client

        # Storage for conversation data
        self._user_transcriptions = []
        self._agent_transcriptions = []
        self._events = []
        self._call_metadata = {}  # Store call-specific metadata like room ID, duration, etc.

        # Queue for async logging
        self._log_q = asyncio.Queue[Union[EventLog, TranscriptionLog, None]]()
        
        # Set up Google Sheets connection if credentials provided
        if self._sheets_credentials_path and self._spreadsheet_id:
            self._setup_google_sheets()

    def _setup_google_sheets(self) -> None:
        """Initialize Google Sheets connection using service account"""
        try:
            # Define the scope - what we're allowed to do with the Sheets API
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]
            
            # Add credentials to the account
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                self._sheets_credentials_path, scope
            )
            
            # Authorize the client
            self._gc = gspread.authorize(creds)
            logging.info("Google Sheets client initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize Google Sheets client: {e}")
            self._gc = None

    # Property getters for accessing conversation data
    @property
    def log(self) -> str | None:
        return self._log

    @property
    def model(self) -> multimodal.MultimodalAgent | None:
        return self._model

    @property
    def user_transcriptions(self) -> List[TranscriptionLog]:
        return self._user_transcriptions

    @property
    def agent_transcriptions(self) -> List[TranscriptionLog]:
        return self._agent_transcriptions

    @property
    def events(self) -> List[EventLog]:
        return self._events
    
    @property
    def call_metadata(self) -> Dict[str, Any]:
        return self._call_metadata

    @log.setter
    def log(self, newlog: str | None) -> None:
        """Change the log file path"""
        self._log = newlog
    
    def set_call_metadata(self, **kwargs) -> None:
        """Store metadata about the current call (room ID, timestamps, etc.)"""
        for key, value in kwargs.items():
            self._call_metadata[key] = value

    async def _main_atask(self) -> None:
        """Main async task that processes the log queue and writes to file"""
        while True:
            log = await self._log_q.get()

            # None is our signal to exit
            if log is None:
                break

            if self._log:
                async with aiofiles.open(self._log, "a") as file:
                    # Handle event logs if we're not in transcriptions-only mode
                    if type(log) is EventLog and not self._transcriptions_only:
                        self._events.append(log)
                        await file.write("\n" + log.time + " " + log.eventname)

                    # Handle transcription logs
                    if type(log) is TranscriptionLog:
                        if log.role == "user":
                            self._user_transcriptions.append(log)
                        else:
                            self._agent_transcriptions.append(log)

                        await file.write(
                            "\n" + log.time + " " + log.role + " " + log.transcription
                        )

    async def aclose(self) -> None:
        """Clean shutdown - export to Google Sheets and close the log queue"""
        # Export to Google Sheets before closing
        if self._gc and self._spreadsheet_id:
            await self._export_to_sheets()
        
        # Signal to _main_atask that we're done
        self._log_q.put_nowait(None)
        await self._main_task

    async def _export_to_sheets(self) -> None:
        """Export all transcriptions and events to Google Sheets asynchronously"""
        try:
            # Use asyncio.to_thread for potentially blocking Google Sheets operations
            def export_operation():
                # Access the spreadsheet
                sheet = self._gc.open_by_key(self._spreadsheet_id)
                
                # Create a new worksheet named with the current timestamp or call ID
                call_id = self._call_metadata.get('room_id', datetime.now().strftime("%Y%m%d_%H%M%S"))
                try:
                    worksheet = sheet.add_worksheet(title=f"Call_{call_id}", rows=1000, cols=20)
                except Exception:
                    # If worksheet already exists, use a unique name with timestamp
                    worksheet = sheet.add_worksheet(
                        title=f"Call_{call_id}_{datetime.now().strftime('%H%M%S')}", 
                        rows=1000, 
                        cols=20
                    )
                
                # Add call metadata as the first section
                metadata_rows = [["Call Metadata", ""]]
                for key, value in self._call_metadata.items():
                    metadata_rows.append([key, str(value)])
                
                metadata_rows.append(["", ""])  # Add empty row for separation
                worksheet.append_rows(metadata_rows)
                
                # Add transcription headers
                worksheet.append_row(["Transcriptions", "", ""])
                worksheet.append_row(["Time", "Role", "Content"])
                
                # Combine and sort all transcriptions by time
                all_transcriptions = []
                for t in self._user_transcriptions:
                    all_transcriptions.append([t.time, t.role, t.transcription])
                for t in self._agent_transcriptions:
                    all_transcriptions.append([t.time, t.role, t.transcription])
                
                # Sort by time
                all_transcriptions.sort(key=lambda x: x[0])
                
                # Add transcription data
                if all_transcriptions:
                    worksheet.append_rows(all_transcriptions)
                
                # Add events if not transcriptions_only
                if not self._transcriptions_only and self._events:
                    worksheet.append_row(["", ""])  # Add empty row for separation
                    worksheet.append_row(["Events", "", ""])
                    worksheet.append_row(["Time", "Event", ""])
                    
                    event_rows = [[e.time, e.eventname, ""] for e in self._events]
                    worksheet.append_rows(event_rows)
                
                return f"Data exported to Google Sheets, worksheet: Call_{call_id}"
            
            result = await asyncio.to_thread(export_operation)
            logging.info(result)
            
        except Exception as e:
            logging.error(f"Failed to export to Google Sheets: {e}")

    def start(self) -> None:
        """Start the persistor and register event handlers for the agent"""
        # Start the async task for processing log entries
        self._main_task = asyncio.create_task(self._main_atask())

        # Register handlers for all relevant agent events
        @self._model.on("user_started_speaking")
        def _user_started_speaking():
            event = EventLog(eventname="user_started_speaking")
            self._log_q.put_nowait(event)

        @self._model.on("user_stopped_speaking")
        def _user_stopped_speaking():
            event = EventLog(eventname="user_stopped_speaking")
            self._log_q.put_nowait(event)

        @self._model.on("agent_started_speaking")
        def _agent_started_speaking():
            event = EventLog(eventname="agent_started_speaking")
            self._log_q.put_nowait(event)

        @self._model.on("agent_stopped_speaking")
        def _agent_stopped_speaking():
            # Log the agent's transcription when they stop talking
            transcription = TranscriptionLog(
                role="agent",
                transcription=(self._model._playing_handle._tr_fwd.played_text)[1:],
            )
            self._log_q.put_nowait(transcription)

            event = EventLog(eventname="agent_stopped_speaking")
            self._log_q.put_nowait(event)

        @self._model.on("user_speech_committed")
        def _user_speech_committed(user_msg: str):  
            # Log what the user said
            transcription = TranscriptionLog(
                role="user", transcription=user_msg  
            )
            self._log_q.put_nowait(transcription)

            event = EventLog(eventname="user_speech_committed")
            self._log_q.put_nowait(event)

        @self._model.on("agent_speech_committed")
        def _agent_speech_committed():
            event = EventLog(eventname="agent_speech_committed")
            self._log_q.put_nowait(event)

        @self._model.on("agent_speech_interrupted")
        def _agent_speech_interrupted():
            event = EventLog(eventname="agent_speech_interrupted")
            self._log_q.put_nowait(event)

        @self._model.on("function_calls_collected")
        def _function_calls_collected():
            event = EventLog(eventname="function_calls_collected")
            self._log_q.put_nowait(event)

        @self._model.on("function_calls_finished")
        def _function_calls_finished():
            event = EventLog(eventname="function_calls_finished")
            self._log_q.put_nowait(event)
            
    def export_sheets_sync(self) -> None:
        """Synchronous version of the sheets export for use in exit handlers"""
        if not self._gc or not self._spreadsheet_id:
            logging.warning("Cannot export to sheets: no Google client or spreadsheet ID")
            return
            
        try:
            # Access the spreadsheet
            sheet = self._gc.open_by_key(self._spreadsheet_id)
            
            # Create a new worksheet named with the current timestamp or call ID
            call_id = self._call_metadata.get('room_id', datetime.now().strftime("%Y%m%d_%H%M%S"))
            try:
                worksheet = sheet.add_worksheet(title=f"Call_{call_id}", rows=1000, cols=20)
            except Exception:
                # If worksheet already exists, use a unique name
                worksheet = sheet.add_worksheet(
                    title=f"Call_{call_id}_{datetime.now().strftime('%H%M%S')}", 
                    rows=1000, 
                    cols=20
                )
            
            # Add call metadata
            metadata_rows = [["Call Metadata", ""]]
            for key, value in self._call_metadata.items():
                metadata_rows.append([key, str(value)])
            
            metadata_rows.append(["", ""])  # Add empty row for separation
            worksheet.append_rows(metadata_rows)
            
            # Add transcription headers
            worksheet.append_row(["Transcriptions", "", ""])
            worksheet.append_row(["Time", "Role", "Content"])
            
            # Combine and sort all transcriptions by time
            all_transcriptions = []
            for t in self._user_transcriptions:
                all_transcriptions.append([t.time, t.role, t.transcription])
            for t in self._agent_transcriptions:
                all_transcriptions.append([t.time, t.role, t.transcription])
            
            # Sort by time
            all_transcriptions.sort(key=lambda x: x[0])
            
            # Add transcription data
            if all_transcriptions:
                worksheet.append_rows(all_transcriptions)
            
            # Add events if not transcriptions_only
            if not self._transcriptions_only and self._events:
                worksheet.append_row(["", ""])  # Add empty row for separation
                worksheet.append_row(["Events", "", ""])
                worksheet.append_row(["Time", "Event", ""])
                
                event_rows = [[e.time, e.eventname, ""] for e in self._events]
                worksheet.append_rows(event_rows)
            
            logging.info(f"Data exported to Google Sheets, worksheet: Call_{call_id}")
            
        except Exception as e:
            logging.error(f"Failed to export to Google Sheets: {e}")


async def entrypoint(ctx: JobContext):
    """Main entry point for the LiveKit agent application"""
    # Initialize the voice agent with OpenAI
    agent = multimodal.MultimodalAgent(
        model=openai.realtime.RealtimeModel(
            voice="alloy",  # Using the "alloy" voice
            temperature=0.8,  # Higher temperature for more varied responses
            instructions="You are a helpful assistant.",  # Basic agent instructions
            turn_detection=openai.realtime.ServerVadOptions(
                threshold=0.6, prefix_padding_ms=200, silence_duration_ms=500
            ),
        ),
    )

    # Create the ConversationPersistor with Google Sheets support
    cp = ConversationPersistor(
        model=agent, 
        log="./log.txt",  # Local log file
        sheets_credentials_path="./credentials.json",  # Google service account
        spreadsheet_id="1mi-vBhWUfhE0B00lbtYaXOsz3iEcUFhI2jkW_z2SgaI"  # Target spreadsheet
    )
    
    # Store important metadata about this call
    cp.set_call_metadata(
        room_id=ctx.room.name,
        start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        agent_voice="alloy"
    )
    
    # Make sure we export data even if the app crashes or is terminated
    def export_on_exit():
        # Update end time at exit
        cp.set_call_metadata(end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        # Export synchronously (not async) since we're shutting down
        cp.export_sheets_sync()
        logging.info("Exported data to Google Sheets on process exit")
    
    # Register the function to be called on normal interpreter exit
    atexit.register(export_on_exit)
    
    # Handle SIGTERM and SIGINT signals (Ctrl+C, kill command, etc.)
    def signal_handler(sig, frame):
        logging.info(f"Received signal {sig}, exporting data before exit")
        export_on_exit()
        # Re-raise the signal after handling
        signal.default_int_handler(sig, frame)
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the conversation persistor
    cp.start()
    
    # Connect to LiveKit and wait for a participant to join
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
    agent.start(ctx.room, participant)

    # Set up some extra logging for visibility
    @agent.on("user_started_speaking")
    def on_user_started_speaking():
        logging.info("Detected user speaking!")

    @agent.on("user_speech_committed")
    def on_user_speech_committed(user_msg: str):  
        logging.info(f"Received user message: {user_msg}")  
        
if __name__ == "__main__":
    # Run the application using LiveKit's CLI helper
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))