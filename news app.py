import streamlit as st
import pandas as pd
import json
import re
import google.generativeai as genai
from supabase import create_client, Client

# 1. 시크릿 설정
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# 2. 클라이언트 초기화
genai.configure(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 페이지 설정
st.set_page_config(page_title="AI 뉴스 저장기", layout="wide")
st.title("🗞️ AI 최신 뉴스 검색 & 자동 저장기 (안정 버전)")

# 탭 구성
tab1, tab2, tab3 = st.tabs(["🔍 검색 및 저장", "📂 저장된 뉴스 보기", "📊 통계 분석"])

# --- Tab 1: 검색 및 저장 ---
with tab1:
    keyword = st.text_input("검색하고 싶은 뉴스 키워드를 입력하세요:", placeholder="예: 생성형 AI 최신 기술")
    search_button = st.button("최신 뉴스 검색 시작")

    if search_button and keyword:
        with st.spinner("Gemini가 최신 정보를 검색 중입니다..."):
            try:
                # 3. 모델 설정 (안정적인 1.5-flash 사용 및 구글 검색 도구 활성화)
                # 표준 라이브러리에서는 도구 이름을 'google_search_retrieval'로 씁니다.
                model = genai.GenerativeModel(
                    model_name='gemini-1.5-flash',
                    tools=[{"google_search_retrieval": {}}]
                )
                
                prompt = f"""
                키워드 '{keyword}'에 대한 가장 최신 뉴스 딱 2건만 검색해줘.
                제목, 출처, 날짜(YYYY-MM-DD), 원본 URL, 3줄 요약을 포함해서 응답해.
                응답은 반드시 JSON 배열 형식으로만 작성해.
                """
                
                response = model.generate_content(prompt)

                # 4. URL 환각 방지 및 데이터 추출 로직
                # JSON 텍스트 추출
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if json_match:
                    news_list = json.loads(json_match.group())
                    
                    # Grounding Metadata에서 실제 참조 URL 추출
                    grounding_metadata = response.candidates[0].grounding_metadata
                    
                    final_news_data = []
                    for item in news_list:
                        real_url = item.get('url', '')
                        
                        # 실제 검색 결과 매칭 시도
                        if hasattr(grounding_metadata, 'grounding_chunks'):
                            for chunk in grounding_metadata.grounding_chunks:
                                if chunk.web and (item['title'] in chunk.web.title or chunk.web.title in item['title']):
                                    candidate_url = chunk.web.uri
                                    if "http" in candidate_url and "grounding-api-redirect" not in candidate_url:
                                        real_url = candidate_url
                                        break
                        
                        item['url'] = real_url
                        item['keyword'] = keyword
                        final_news_data.append(item)

                    # 5. 화면 출력 및 DB 저장
                    success_count = 0
                    skip_count = 0
                    
                    cols = st.columns(2)
                    for idx, news in enumerate(final_news_data):
                        with cols[idx]:
                            st.info(f"**{news['title']}**")
                            st.caption(f"{news.get('source', '알 수 없음')} | {news.get('news_date', '')}")
                            st.write(news.get('summary', '요약 정보 없음'))
                            st.markdown(f"[기사 읽기]({news['url']})")
                            
                            # Supabase 저장
                            try:
                                supabase.table("news_history").insert({
                                    "keyword": news['keyword'],
                                    "title": news['title'],
                                    "source": news.get('source', ''),
                                    "news_date": news.get('news_date', ''),
                                    "url": news['url'],
                                    "summary": news.get('summary', '')
                                }).execute()
                                success_count += 1
                            except Exception as e:
                                if '23505' in str(e): # 중복 키 에러
                                    skip_count += 1
                                else:
                                    st.error(f"저장 오류: {e}")

                    st.toast(f"✅ 처리 완료! (신규: {success_count}, 중복: {skip_count})")
                else:
                    st.warning("JSON 형식을 추출할 수 없습니다. 다시 시도해 주세요.")

            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

# --- Tab 2: 저장된 뉴스 보기 ---
with tab2:
    st.subheader("최근 저장된 뉴스")
    try:
        res = supabase.table("news_history").select("*").order("created_at", desc=True).limit(20).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            st.dataframe(df[['created_at', 'keyword', 'title', 'source', 'url']], use_container_width=True)
            for _, row in df.head(5).iterrows():
                with st.expander(f"{row['title']}"):
                    st.write(row['summary'])
                    st.link_button("원문 보기", row['url'])
        else:
            st.info("데이터가 없습니다.")
    except Exception as e:
        st.error(f"DB 로드 오류: {e}")

# --- Tab 3: 통계 분석 ---
with tab3:
    st.subheader("검색 키워드 분포")
    try:
        res = supabase.table("news_history").select("keyword").execute()
        if res.data:
            df_stats = pd.DataFrame(res.data)
            st.bar_chart(df_stats['keyword'].value_counts())
    except Exception as e:
        st.error(f"통계 로드 오류: {e}")
