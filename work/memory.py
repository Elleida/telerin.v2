"""
Sistema de Memoria Contextual para Chat TeleRadio

Este módulo implementa la memoria conversacional que permite al chatbot:
- Recordar el contexto de la conversación
- Mantener entidades mencionadas (años, canales, programas, etc.)
- Mejorar queries con contexto previo
- Proporcionar experiencia de chat fluida
"""

from typing import List, Dict, Any, Optional, TypedDict
from datetime import datetime
import json
import re


class ConversationTurn(TypedDict):
    """Representa un turno individual en la conversación"""
    turn_id: int
    timestamp: str
    user_query: str
    query_type: str  # greeting, system_info, data_search
    enhanced_query: Optional[str]  # Query con contexto
    search_results: List[Dict]
    response: str
    entities_found: Dict  # Años, canales, programas, etc.
    relevance_score: float  # Relevancia para el siguiente turno


class ConversationMemory:
    """
    Almacena y gestiona el contexto de la conversación
    
    Mantiene:
    - Histórico completo de mensajes
    - Resumen de contexto actual
    - Entidades mencionadas
    - Historial de búsquedas
    """
    
    def __init__(self, max_history: int = 100, context_window: int = 5):
        """
        Inicializa el sistema de memoria
        
        Args:
            max_history: Máximo de turnos a guardar
            context_window: Últimos N turnos como contexto activo
        """
        self.messages: List[ConversationTurn] = []
        self.context_summary: Dict[str, Any] = {}
        self.entities: Dict[str, Any] = {
            "years": set(),
            "channels": set(),
            "programs": set(),
            "topics": set(),
            "products": set(),
            "content_types": set(),
        }
        self.search_history: List[Dict[str, Any]] = []
        self.last_results: List[Dict] = []
        self.max_history = max_history
        self.context_window = context_window
    
    def add_turn(
        self,
        user_query: str,
        response: str,
        query_type: str = "data_search",
        enhanced_query: Optional[str] = None,
        search_results: Optional[List[Dict]] = None,
        entities_found: Optional[Dict] = None,
        relevance_score: float = 1.0
    ):
        """
        Agrega un turno (pregunta + respuesta) al histórico
        
        Args:
            user_query: Pregunta del usuario
            response: Respuesta generada
            query_type: Tipo de query (greeting, system_info, data_search)
            enhanced_query: Query mejorada con contexto
            search_results: Resultados de búsqueda
            entities_found: Entidades encontradas en este turno
            relevance_score: Relevancia para el siguiente turno
        """
        turn = ConversationTurn(
            turn_id=len(self.messages),
            timestamp=datetime.now().isoformat(),
            user_query=user_query,
            query_type=query_type,
            enhanced_query=enhanced_query,
            search_results=search_results or [],
            response=response,
            entities_found=entities_found or {},
            relevance_score=relevance_score
        )
        
        self.messages.append(turn)
        
        # Actualizar entidades globales
        if entities_found:
            self._update_global_entities(entities_found)
        
        # Guardar resultados
        if search_results:
            self.last_results = search_results
            self._add_to_search_history(user_query, enhanced_query, search_results)
        
        # Actualizar resumen de contexto
        self._update_context_summary()
        
        # Limitar histórico si excede el máximo
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]
    
    def _update_global_entities(self, entities: Dict[str, Any]):
        """Actualiza las entidades globales con nuevas entidades encontradas"""
        for key, values in entities.items():
            if key in self.entities:
                if isinstance(values, (list, set)):
                    self.entities[key].update(values)
                elif values:  # Si es un valor único
                    self.entities[key].add(values)
    
    def _add_to_search_history(
        self,
        query: str,
        enhanced_query: Optional[str],
        results: List[Dict]
    ):
        """Agrega una búsqueda al historial"""
        self.search_history.append({
            "turn": len(self.messages),
            "query": query,
            "enhanced_query": enhanced_query,
            "results_count": len(results),
            "timestamp": datetime.now().isoformat(),
            "reused_context": bool(enhanced_query and enhanced_query != query)
        })
    
    def _update_context_summary(self):
        """Actualiza el resumen de contexto basado en turnos recientes"""
        recent_turns = self.get_recent_turns(self.context_window)
        
        if not recent_turns:
            return
        
        # Extraer información de turnos recientes
        recent_years = set()
        recent_channels = set()
        recent_topics = set()
        recent_content_types = set()
        
        for turn in recent_turns:
            entities = turn.get("entities_found", {})
            recent_years.update(entities.get("years", []))
            recent_channels.update(entities.get("channels", []))
            recent_topics.update(entities.get("topics", []))
            recent_content_types.update(entities.get("content_types", []))
        
        # Construir resumen
        self.context_summary = {
            "time_period": self._format_years(recent_years),
            "content_types": list(recent_content_types),
            "main_topics": list(recent_topics),
            "channels": list(recent_channels),
            "last_search_intent": recent_turns[-1].get("query_type", "unknown"),
            "conversation_focus": self._infer_conversation_focus(recent_turns)
        }
    
    def _format_years(self, years: set) -> str:
        """Formatea un conjunto de años en un string legible"""
        if not years:
            return "No especificado"
        
        years_list = sorted(years)
        if len(years_list) == 1:
            return str(years_list[0])
        elif len(years_list) == 2:
            return f"{years_list[0]}-{years_list[1]}"
        else:
            return f"{min(years_list)}-{max(years_list)}"
    
    def _infer_conversation_focus(self, recent_turns: List[ConversationTurn]) -> str:
        """Infiere el foco de la conversación basado en turnos recientes"""
        if not recent_turns:
            return "General"
        
        # Analizar queries recientes
        queries = [turn.get("user_query", "") for turn in recent_turns]
        combined_text = " ".join(queries).lower()
        
        # Determinar foco basado en palabras clave
        if any(word in combined_text for word in ["televisión", "tv", "programa tv"]):
            return "Programación de Televisión"
        elif any(word in combined_text for word in ["radio", "programa radio"]):
            return "Programación de Radio"
        elif any(word in combined_text for word in ["publicidad", "anuncio", "anunciante"]):
            return "Publicidad"
        elif any(word in combined_text for word in ["artículo", "reportaje", "editorial"]):
            return "Contenido Editorial"
        else:
            return "Exploración General"
    
    def get_recent_turns(self, n: int = 5) -> List[ConversationTurn]:
        """
        Obtiene los últimos N turnos
        
        Args:
            n: Número de turnos a recuperar
            
        Returns:
            Lista de turnos recientes
        """
        return self.messages[-n:] if self.messages else []
    
    def extract_context(self) -> Dict[str, Any]:
        """
        Extrae el contexto actual de la conversación
        
        Returns:
            Diccionario con contexto resumido
        """
        return {
            "context_summary": self.context_summary,
            "global_entities": {
                key: list(values) for key, values in self.entities.items()
            },
            "recent_searches": self.search_history[-5:] if self.search_history else [],
            "turn_count": len(self.messages),
            "has_context": len(self.messages) > 0
        }
    
    def get_enhanced_query(self, new_query: str) -> Optional[str]:
        """
        Mejora una query con contexto previo
        
        Args:
            new_query: Query original del usuario
            
        Returns:
            Query mejorada con contexto, o None si no hay mejora
        """
        # Si no hay contexto previo, retornar None
        if not self.messages:
            print("   [get_enhanced_query] No hay mensajes en memoria")
            return None
        
        print(f"   [get_enhanced_query] Intentando mejorar: '{new_query}'")
        
        # Importar QueryEnhancer (lazy import para evitar circular dependency)
        from query_enhancer import QueryEnhancer
        
        enhancer = QueryEnhancer(self)
        result = enhancer.enhance_query(new_query)
        
        if result:
            print(f"   [get_enhanced_query] ✓ Mejorada a: '{result}'")
        else:
            print(f"   [get_enhanced_query] ✗ No se pudo mejorar")
        
        return result
    
    def clear(self):
        """Limpia toda la conversación y reinicia el estado"""
        self.messages = []
        self.context_summary = {}
        self.entities = {
            "years": set(),
            "channels": set(),
            "programs": set(),
            "topics": set(),
            "products": set(),
            "content_types": set(),
        }
        self.search_history = []
        self.last_results = []
    
    def get_memory_summary(self) -> str:
        """
        Retorna un resumen legible del contexto para debugging
        
        Returns:
            String formateado con resumen de memoria
        """
        if not self.messages:
            return "🧠 Memoria Vacía - No hay conversación activa"
        
        summary_lines = [
            "🧠 RESUMEN DE MEMORIA",
            "=" * 50,
            f"📊 Total de turnos: {len(self.messages)}",
            f"🔍 Búsquedas ejecutadas: {len(self.search_history)}",
            "",
            "📍 Contexto Actual:",
        ]
        
        for key, value in self.context_summary.items():
            summary_lines.append(f"  • {key}: {value}")
        
        summary_lines.append("")
        summary_lines.append("🏷️ Entidades Mencionadas:")
        
        for entity_type, values in self.entities.items():
            if values:
                summary_lines.append(f"  • {entity_type}: {', '.join(map(str, list(values)[:5]))}")
        
        return "\n".join(summary_lines)
    
    def export_conversation(self) -> str:
        """
        Exporta la conversación en formato JSON
        
        Returns:
            String JSON con toda la conversación
        """
        export_data = {
            "metadata": {
                "turn_count": len(self.messages),
                "search_count": len(self.search_history),
                "exported_at": datetime.now().isoformat()
            },
            "context_summary": self.context_summary,
            "entities": {
                key: list(values) for key, values in self.entities.items()
            },
            "conversation": [
                {
                    "turn_id": turn["turn_id"],
                    "timestamp": turn["timestamp"],
                    "user_query": turn["user_query"],
                    "query_type": turn["query_type"],
                    "enhanced_query": turn.get("enhanced_query"),
                    "response": turn["response"],
                    "entities_found": turn.get("entities_found", {}),
                }
                for turn in self.messages
            ]
        }
        
        return json.dumps(export_data, indent=2, ensure_ascii=False)
    
    def is_contextual_follow_up(self, query: str) -> bool:
        """
        Determina si una query es un follow-up contextual usando LLM
        
        Args:
            query: Query del usuario
            
        Returns:
            True si parece ser un follow-up que necesita contexto
        """
        if not self.messages:
            return False
        
        # Usar LLM para análisis semántico del contexto
        try:
            from llm_context_analyzer import get_llm_analyzer
            
            last_turn = self.get_last_turn()
            if not last_turn:
                return False
            
            last_query = last_turn.get("user_query", "")
            last_response = last_turn.get("assistant_response", "")
            
            analyzer = get_llm_analyzer()
            analysis = analyzer.is_contextual_follow_up(
                current_query=query,
                last_query=last_query,
                last_response=last_response
            )
            
            # Considerar follow-up si confianza > 0.6
            is_follow_up = analysis.get("is_follow_up", False)
            confidence = analysis.get("confidence", 0.0)
            
            print(f"🤖 Análisis LLM follow-up: {is_follow_up} (confianza: {confidence:.2f})")
            
            return is_follow_up and confidence > 0.6
            
        except Exception as e:
            print(f"⚠️ Error en análisis LLM, usando fallback: {e}")
            # Fallback simple si el LLM falla
            return self._simple_follow_up_detection(query)
    
    def _simple_follow_up_detection(self, query: str) -> bool:
        """
        Detección simple de follow-up como fallback
        """
        query_lower = query.lower().strip()
        
        # Patrones más evidentes
        obvious_patterns = [
            r"^¿?y\s+(en|de|sobre|para)",
            r"^¿?también\s+",
            r"^¿?además\s+",
            r"^¿?cuántos?\s+",
        ]
        
        for pattern in obvious_patterns:
            if re.match(pattern, query_lower):
                return True
        
        # Preguntas muy cortas (< 6 palabras) probablemente son follow-ups
        return len(query_lower.split()) <= 5
    
    def get_last_turn(self) -> Optional[ConversationTurn]:
        """Obtiene el último turno de conversación"""
        return self.messages[-1] if self.messages else None
    
    def __len__(self) -> int:
        """Retorna el número de turnos en la memoria"""
        return len(self.messages)
    
    def __repr__(self) -> str:
        """Representación string de la memoria"""
        return f"ConversationMemory(turns={len(self.messages)}, searches={len(self.search_history)})"


# Utilidades para debugging
def print_memory_state(memory: ConversationMemory):
    """Imprime el estado actual de la memoria (para debugging)"""
    print("\n" + "="*60)
    print(memory.get_memory_summary())
    print("="*60 + "\n")
