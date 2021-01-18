# coding=utf-8
import streamlit as st
import pandas as pd
import base64
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options

JOBVIEW = 'https://kozigallas.gov.hu/pages/jobviewer.aspx?ID='
MEGYEK = ('Bács-Kiskun megye', 'Baranya megye', 'Békés megye', 'Borsod-Abaúj-Zemplén megye',
          'Budapest', 'Csongrád-megye', 'Fejér-megye', 'Győr-Moson-Sopron megye', 'Hajdú-Bihar megye',
          'Heves megye', 'Jász-Nagykun-Szolnok megye', 'Komárom-Esztergom megye', 'Nógrád megye',
          'Pest megye', 'Somogy megye', 'Szabolcs-Szatmár-Bereg megye', 'Tolna megye', 'Vas megye',
          'Veszprém megye', 'Zala megye')

# Function of waiting until the present of the element on the web page
def waiting_func(driver, by_variable, attribute):
    try:
        WebDriverWait(driver, 10).until(lambda x: x.find_element(by=by_variable, value=attribute))
    except:
        st.write('{} {} not found'.format(by_variable, attribute))
        

@st.cache(suppress_st_warning=True)
def getData(megye):
    list_of_jobs = []
    chrome_options = Options()  
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-notifications')
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_experimental_option('excludeSwitches', ['disable-logging'])

    driver = webdriver.Chrome(options=chrome_options)
    driver.get('https://kozigallas.gov.hu/publicsearch.aspx')
    waiting_func(driver, 'id', 'ctl00_ContentPlaceHolder1_JobSearchForm1_btnSearch')

    driver.find_element_by_id('ctl00_ContentPlaceHolder1_JobSearchForm1_lnkDetailedSearch').click()
    driver.find_element_by_id('ctl00_ContentPlaceHolder1_JobSearchForm1_ddlCounty').send_keys(megye)
    driver.find_element_by_id('ctl00_ContentPlaceHolder1_JobSearchForm1_btnSearch').click()

    while True:
        waiting_func(driver, 'class name', 'joblist')
        joblist = driver.find_element_by_class_name('joblist')
        trs = joblist.find_elements_by_css_selector('tr')
        for tr in trs:
            if not 'Jelent' in tr.text:
                tds = tr.find_elements_by_css_selector('td')
                aLink = JOBVIEW + tr.find_elements_by_css_selector('.jobapplication')[0].get_attribute('href')[23:33]
                one_job = [td.text for td in tds]
                one_job[1] = one_job[1].lower()
                one_job.append(aLink)
                list_of_jobs.append(one_job)
        try:
            driver.find_element_by_id('ctl00_ContentPlaceHolder1_JobSearchForm1_JobList1_linkNext2').click()
        except:
            break
    driver.close()
    return list_of_jobs

def filedownload(df, filename):
    csv = df.to_csv(index = False)
    b64 = base64.b64encode(csv.encode()).decode()
    href = f'<a href="data:file/csv;base64,{b64}" download="{filename}.csv">CSV File Letöltése</a>'
    return href

def main():
    st.title('Közigállás ajánlatok lekérdezése')

    megye = st.selectbox('Megye', MEGYEK, index=7)
    list_of_jobs = getData(megye)
    st.write(f'{len(list_of_jobs)} állásajánlat van.')

    with st.beta_expander('Adattábla'):
        df = pd.DataFrame(data=list_of_jobs)
        df.drop([3, 5,6], axis=1, inplace=True)
        df.rename(columns = {0: 'Dátum', 1: 'Állás', 2: 'Hely', 4:'Pozíció', 7: 'Link'}, inplace=True)
        st.dataframe(df)
        st.markdown(filedownload(df, megye), unsafe_allow_html=True)

    with st.beta_expander('Részletes lista'):
        for l in list_of_jobs:
            st.markdown(f'***{l[1]}*** - {l[2]}')
            st.markdown(f'Határidő: *{l[0]}* Munkavégzés helye: *{l[3].replace(megye + " ,","")}*')
            st.markdown(f'<a href="{l[7]}" target="_blank">Megnézem</a>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()