import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Enum, Float, ForeignKey,
    Integer, JSON, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class SimulatorType(str, enum.Enum):
    steady = "STEADY"
    unsteady = "UNSTEADY"


class SimulationStatus(str, enum.Enum):
    pending = "PENDING"
    meshing = "MESHING"
    running = "RUNNING"
    done = "DONE"
    failed = "FAILED"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.user)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    owner = relationship("User", back_populates="projects")
    geometries = relationship("Geometry", back_populates="project", cascade="all, delete-orphan")


class Geometry(Base):
    __tablename__ = "geometries"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    name = Column(String(128), nullable=False, default="")
    description = Column(Text, default="")
    cad_file_path = Column(String(512), default="")
    stl_file_path = Column(String(512), default="")
    transform_info = Column(JSON, default=None)  # {"mm_to_m": bool, "scale": float, "yaw": ..., "pitch": ..., "roll": ...}
    created_at = Column(DateTime(timezone=True), default=utcnow)

    project = relationship("Project", back_populates="geometries")
    simulations = relationship("Simulation", back_populates="geometry", cascade="all, delete-orphan")


class Simulation(Base):
    __tablename__ = "simulations"

    id = Column(Integer, primary_key=True)
    geometry_id = Column(Integer, ForeignKey("geometries.id"), nullable=False)
    name = Column(String(128), nullable=False, default="")
    description = Column(Text, default="")
    solver_type = Column(Enum(SimulatorType), nullable=False, default=SimulatorType.steady)
    status = Column(Enum(SimulationStatus), nullable=False, default=SimulationStatus.pending)

    # Computed case directory
    case_dir = Column(String(512), default="")

    # Cluster job
    job_id = Column(String(64), default="")

    # Wind direction
    yaw_deg = Column(Float, default=0.0)
    pitch_deg = Column(Float, default=0.0)
    roll_deg = Column(Float, default=0.0)

    # All OpenFOAM parameters as JSON
    parameters = Column(JSON, default=dict)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    geometry = relationship("Geometry", back_populates="simulations")
    messages = relationship("ChatMessage", back_populates="simulation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    simulation_id = Column(Integer, ForeignKey("simulations.id"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    simulation = relationship("Simulation", back_populates="messages")
