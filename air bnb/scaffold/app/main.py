from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .database import Base, SessionLocal, engine
from .indexes import apply_index_strategy
from .routes import router
from .seed import has_data, seed_small
from .settings import settings


def _bootstrap():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not has_data(db):
            seed_small(db)
    finally:
        db.close()
    apply_index_strategy(engine, settings.index_strategy)


_bootstrap()

app = FastAPI(title="Airbnb Booking Playground")
app.include_router(router)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
