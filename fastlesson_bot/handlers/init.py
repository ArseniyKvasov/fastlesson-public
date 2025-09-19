from .start import router as start_router
from .teacher import router as teacher_router
from .payments import router as payments_router

all_handlers = [start_router, teacher_router, payments_router]
