# ThaiTranscriber

A minimal Python CLI tool for transcribing Thai audio files using the Typhoon ASR API.

## Features

- CLI interface for transcribing Thai audio files
- Support for multiple audio formats (.wav, .mp3, .flac, .ogg, .opus)
- Configurable output formats (plain text or JSON with metadata)
- Environment-based configuration
- Comprehensive error handling and logging
- Optimized for Thai language transcription

## Requirements

- Python 3.11 or higher
- Typhoon ASR API key (get from https://playground.opentyphoon.ai/asr)

## Installation

1. Clone or download this repository:
```bash
git clone <repository-url>
cd ThaiTranscriber
```

2. Install dependencies:
```bash
pip3 install -r requirements.txt
```

3. Configure your API key:
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your Typhoon ASR API key
# Get your API key from: https://playground.opentyphoon.ai/asr
```

## Usage

### Basic Transcription

Transcribe an audio file to text:
```bash
python transcribe.py --file audio.wav
```

This will create `audio.txt` in the same directory as your audio file.

### JSON Output with Metadata

Save transcription with metadata in JSON format:
```bash
python transcribe.py --file audio.mp3 --output-format json
```

### Save Both Formats

Generate both text and JSON outputs:
```bash
python transcribe.py --file audio.wav --output-format both
```

### Custom Output Path

Specify a custom output file:
```bash
python transcribe.py --file audio.wav --output transcript.txt
```

### Custom Output Directory

Save to a specific directory:
```bash
python transcribe.py --file audio.wav --output-dir ./transcripts/
```

### Advanced Options

```bash
# Use a custom .env file
python transcribe.py --file audio.wav --env-file production.env

# Override language setting
python transcribe.py --file audio.wav --language th

# Adjust temperature for sampling
python transcribe.py --file audio.wav --temperature 0.0

# Enable debug logging
python transcribe.py --file audio.wav --log-level DEBUG

# Quiet mode (no console output)
python transcribe.py --file audio.wav --quiet
```

## Configuration

### Environment Variables

Create a `.env` file in the project directory with the following variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TYPHOON_API_KEY` | Yes | - | Your Typhoon ASR API key |
| `TYPHOON_BASE_URL` | No | `https://api.opentyphoon.ai/v1` | API endpoint |
| `TYPHOON_MODEL` | No | `typhoon-asr-realtime` | Model name |
| `TYPHOON_LANGUAGE` | No | `th` | Language code (Thai) |
| `TYPHOON_RESPONSE_FORMAT` | No | `json` | API response format |
| `TYPHOON_TEMPERATURE` | No | `0.0` | Sampling temperature (0.0-1.0) |
| `TYPHOON_ENABLE_TIMESTAMPS` | No | `true` | Enable word-level timestamps |
| `TYPHOON_ENABLE_WORD_CONFIDENCE` | No | `true` | Enable confidence scores |
| `TYPHOON_LOG_LEVEL` | No | `INFO` | Logging level |

### Command-Line Arguments

All configuration can be overridden via command-line arguments:

```bash
python transcribe.py --help
```

## Project Structure

```
ThaiTranscriber/
├── transcribe.py          # Main CLI entry point
├── src/
│   ├── __init__.py       # Package initialization
│   ├── client.py         # Typhoon ASR API client wrapper
│   ├── config.py         # Configuration management
│   └── utils.py          # Utility functions
├── requirements.txt      # Python dependencies
├── .env.example         # Environment configuration template
├── .gitignore           # Git ignore rules
└── README.md            # This file
```

## Supported Audio Formats

- WAV (.wav)
- MP3 (.mp3)
- FLAC (.flac)
- OGG (.ogg)
- OPUS (.opus)

## Output Formats

### Text Format (.txt)

Plain text transcription:
```
สวัสดีครับ ยินดีต้อนรับ
```

### JSON Format (.json)

Transcription with metadata:
```json
{
  "text": "สวัสดีครับ ยินดีต้อนรับ",
  "language": "th",
  "duration": 2.5
}
```

## Error Handling

The tool provides clear error messages for common issues:

- **Missing API Key**: Prompts to configure `TYPHOON_API_KEY`
- **Authentication Errors**: Validates API key
- **Rate Limits**: Informs about API rate limits (100 requests/minute)
- **Invalid Audio Format**: Lists supported formats
- **File Not Found**: Validates file paths
- **Network Errors**: Reports timeout and connection issues

## Logging

Logging is configured to show:
- Timestamp
- Module name
- Log level
- Message

Available log levels:
- `DEBUG`: Detailed diagnostic information
- `INFO`: General information (default)
- `WARNING`: Warning messages
- `ERROR`: Error messages

Set via environment variable or command-line:
```bash
python transcribe.py --file audio.wav --log-level DEBUG
```

## API Information

- **Provider**: OpenTyphoon AI
- **Endpoint**: https://api.opentyphoon.ai/v1
- **Model**: typhoon-asr-realtime
- **Rate Limit**: 100 requests per minute
- **Documentation**: https://docs.opentyphoon.ai/th/asr/

### Getting an API Key

1. Visit https://playground.opentyphoon.ai/asr
2. Sign up or log in
3. Generate an API key
4. Add it to your `.env` file

## Best Practices

### For Best Transcription Accuracy

1. **Audio Quality**: Use high-quality audio recordings
2. **Format**: WAV or FLAC for best quality
3. **Sample Rate**: 16kHz or higher recommended
4. **Background Noise**: Minimize background noise
5. **Temperature**: Keep at 0.0 for deterministic results

### For Large Files

- Check API documentation for file size limits
- Consider splitting very long audio files
- Use appropriate timeouts for large files

## Troubleshooting

### "pip not found"
Use `pip3` instead:
```bash
pip3 install -r requirements.txt
```

### "TYPHOON_API_KEY environment variable is required"
1. Verify `.env` file exists in project directory
2. Check that `TYPHOON_API_KEY` is set in `.env`
3. Ensure no typos in variable name
4. Verify no extra spaces around the API key

### "Authentication failed"
1. Get a new API key from https://playground.opentyphoon.ai/asr
2. Update your `.env` file
3. Ensure the API key is copied correctly

### "Rate limit exceeded"
Wait 60 seconds before making more requests. The API allows 100 requests per minute.

### "Invalid audio format"
Ensure your audio file is in a supported format: .wav, .mp3, .flac, .ogg, or .opus

## License

This project is provided as-is for use with the Typhoon ASR API.

## Credits

- **Typhoon ASR API**: OpenTyphoon AI (https://opentyphoon.ai)
- **OpenAI SDK**: Used for API communication

## Support

For issues related to:
- **This tool**: Check the troubleshooting section above
- **Typhoon ASR API**: Visit https://docs.opentyphoon.ai/th/asr/
- **API access**: Contact OpenTyphoon AI support
