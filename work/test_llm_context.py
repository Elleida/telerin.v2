"""
Script de prueba para verificar el análisis LLM de contexto
"""

from llm_context_analyzer import LLMContextAnalyzer

def test_follow_up_detection():
    """Prueba la detección de follow-ups con LLM"""
    
    analyzer = LLMContextAnalyzer()
    
    # Caso 1: Follow-up obvio
    print("\n" + "="*60)
    print("CASO 1: Follow-up obvio")
    print("="*60)
    result = analyzer.is_contextual_follow_up(
        current_query="¿Y en televisión?",
        last_query="¿Qué programas de radio había en 1962?",
        last_response="En 1962 había varios programas de radio..."
    )
    print(f"Resultado: {result}")
    
    # Caso 2: Follow-up implícito (el caso reportado)
    print("\n" + "="*60)
    print("CASO 2: Follow-up implícito con referencia")
    print("="*60)
    result = analyzer.is_contextual_follow_up(
        current_query="¿Qué tipo de programa era?",
        last_query='¿Quién presentaba el programa "Caras Nuevas"?',
        last_response="El programa Caras Nuevas era presentado por..."
    )
    print(f"Resultado: {result}")
    
    # Caso 3: No es follow-up
    print("\n" + "="*60)
    print("CASO 3: Pregunta independiente")
    print("="*60)
    result = analyzer.is_contextual_follow_up(
        current_query="¿Qué programas culturales había en 1965?",
        last_query="¿Quién presentaba Caras Nuevas?",
        last_response="El programa era presentado por..."
    )
    print(f"Resultado: {result}")
    
    # Caso 4: Enhancement de query
    print("\n" + "="*60)
    print("CASO 4: Mejora de query con contexto")
    print("="*60)
    
    context = {
        "context_summary": {},
        "global_entities": {
            "programs": ["Caras Nuevas"],
            "years": [1962]
        },
        "last_query": '¿Quién presentaba "Caras Nuevas"?'
    }
    
    result = analyzer.enhance_query_with_context(
        current_query="¿Qué tipo de programa era?",
        conversation_context=context,
        last_query='¿Quién presentaba "Caras Nuevas"?'
    )
    print(f"Resultado: {result}")

if __name__ == "__main__":
    try:
        test_follow_up_detection()
        print("\n✅ Pruebas completadas")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
