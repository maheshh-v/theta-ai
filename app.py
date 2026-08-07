"""
Entry point for the Personal AI Assistant.

Run locally:
    python app.py

On HuggingFace Spaces (Gradio SDK), this file is auto-detected and executed;
`demo.launch()` binds to the port the platform provides.
"""

from ui.app import build_demo

# Exposed at module level so HuggingFace Spaces can discover the Gradio app.
demo = build_demo()

if __name__ == "__main__":
    import os

    demo.queue()
    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        show_error=True,
    )
