# -*- coding: utf-8 -*-
"""浏览器补全工具：把浏览器查到的公司校招信息补进残缺岗位并重新打分。

用法：python _browser_enrich.py [公司名关键词...]
不传参数时处理 ENRICH 表中全部公司；传参时按公司名包含匹配过滤。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from job_assistant.config.settings import load_config  # noqa: E402
from job_assistant.matcher.scorer import JobScorer  # noqa: E402
from job_assistant.parser.llm_client import HttpLLMClient  # noqa: E402
from job_assistant.parser.schema import JobPosting  # noqa: E402

# 浏览器补全信息表：company -> 字段更新 + 打分用外部资料（真实浏览器检索结果）
ENRICH = {
    "普渡机器人": {
        "fields": {
            "job_title": "后端工程师(J12659·Go主栈)、Agent开发工程师(J12678·Java可)、AI Infra基础架构等2027校招",
            "location": "广东省·深圳市",
            "tech_stack": "后端工程师：Golang/grpc/kratos/MySQL/Redis；Agent开发：Go优先，C++/Java/Python亦可",
            "apply_method": "https://pudutech.zhiye.com/campus/jobs",
            "source": "浏览器补全-普渡官方招聘平台",
        },
        "source": "https://pudutech.zhiye.com/campus/jobs",
        "external": (
            "【浏览器补全·普渡机器人2027校招官网】深圳市普渡科技股份有限公司，简称普渡机器人(Pudu Robotics)，"
            "商用服务机器人企业，2016年成立，总部广东深圳。2027届校园招聘共92个岗位，软件类12个含："
            "后端工程师(J12659，深圳)职责为云端服务模块设计开发、机器人解决方案跨系统对接，要求Golang、"
            "grpc、kratos框架、MySQL、Redis（Go为主语言）；Agent开发工程师(J12678，深圳)负责AI服务架构、"
            "LLM集成、RAG、MCP、Function Call、Agent架构，要求Go优先、掌握C++/Java/Python亦可；"
            "另有AI Infra基础架构(SRE)、数据平台、模型闭环、端侧智能工程师，AI Builder等。"
            "工作地点主要为深圳，部分北京/成都。"
        ),
    },
    "纵维立方": {
        "fields": {
            "job_title": "软件开发岗(10-15K·本科)、嵌入式开发岗、软件产品岗等2027校招",
            "location": "广东省·深圳市龙岗区",
            "education": "本科及以上（软件开发岗本科）",
            "tech_stack": "软件开发岗未注明具体语言（计算机/软件工程/人工智能专业）；嵌入式开发需C/C++",
            "apply_method": "https://anycubic.zhiye.com（邮箱hrzp@anycubic.com）",
            "source": "浏览器补全-西电就业网校招公告",
        },
        "source": "https://job.xidian.edu.cn/campus/view/id/757021",
        "external": (
            "【浏览器补全·纵维立方2027校招（西电就业网官方公告）】深圳市纵维立方科技有限公司(Anycubic)，"
            "2015年成立，3D打印机研产销一体高新企业，2000多名员工，国家级专精特新小巨人，业务覆盖全球200+国家。"
            "2027届校招岗位：软件开发岗(8名，10-15K/深圳/本科，计算机/软件工程/人工智能专业)、"
            "嵌入式开发岗(5名，10-15K/深圳/本科)、软件产品岗(5名，10-25K/深圳)、几何图形/运动控制/AI视觉算法岗"
            "（15-35K，硕士为主）、硬件设计/机械结构/测试等。福利：六险一金、带薪年假、年度体检。"
            "投递：anycubic.zhiye.com 或 hrzp@anycubic.com。"
        ),
    },
    "宇树科技股份有限公司": {
        "fields": {
            "job_title": "JAVA后端开发工程师(J10096)、AI Infra、数据管线、具身智能软件等（2027校招/在招）",
            "location": "浙江省·杭州市",
            "tech_stack": "JAVA后端开发工程师(J10096)；AI Infra/数据管线/具身智能软件等（偏AI系统）",
            "apply_method": "https://www.unitree.com/cn/position",
            "source": "浏览器补全-宇树官网招聘页",
        },
        "source": "https://www.unitree.com/cn/position",
        "external": (
            "【浏览器补全·宇树科技官网招聘页】宇树科技(Unitree)，全球领先的具身智能机器人企业，"
            "专注高性能通用具身机器人全链路自研，四足机器人全球市占领先，总部浙江杭州。"
            "2027届校招含AI算法、具身智能、语音/视觉大模型、AI Infra、数据管线等方向；"
            "官网在招岗位含JAVA后端开发工程师(J10096，杭州市，技术类研发部，负责移动端产品后台服务端搭建)；"
            "所有技术岗位工作地点均为杭州。"
        ),
    },
    "沐曦集成电路（上海）股份有限公司": {
        "fields": {
            "job_title": "软件类（AI、驱动、编译器、算子开发、科学计算生态）等2027校招",
            "location": "上海/成都/深圳/南京/北京等多地",
            "tech_stack": "软件类岗位：AI、驱动、编译器、算子开发、科学计算生态（偏底层系统软件）",
            "apply_method": "官网网申（扫码投递，7/31-9/30）",
            "source": "浏览器补全-牛企直聘转载官方公告",
        },
        "source": "https://campus.niuqizp.com/job-vkl5zMzLz.html",
        "external": (
            "【浏览器补全·沐曦股份2027校招公告】沐曦集成电路（上海）股份有限公司，2020年成立，上海总部，"
            "科创板上市，国产高性能GPU芯片龙头（训练/推理/通用计算GPU），核心研发团队超1000人。"
            "2027届校招网申2026/07/31~2026/09/30。岗位分硬件类（架构/芯片设计/芯片验证等）、"
            "软件类（AI、驱动、编译器、算子开发、科学计算生态、体系结构）、商务类、管理类。"
            "工作地点：上海、南京、北京、成都、深圳、杭州、长沙。福利：13薪+绩效、公积金12%、补充商业保险。"
        ),
    },
    "华讯科技": {
        "fields": {
            "job_title": "软件工程师（20名）等2027校招",
            "location": "陕西省·西安市（高新区）",
            "education": "统招本科（一本院校）及以上",
            "tech_stack": "C/C++/C#/Java至少一种；SQL Server/Oracle/MySQL/PostgreSQL至少一种",
            "apply_method": "校招邮箱 hr.students@huatek.com",
            "source": "浏览器补全-陕西科技大学就业网公告",
        },
        "source": "https://51uns.bysjy.com.cn/Lecture/Details/714261?schoolCode=10708",
        "external": (
            "【浏览器补全·西安华讯2027校招（高校就业网公告）】西安华讯科技有限责任公司，华泰软件(Huatek)旗下，"
            "华泰软件1993年由中国惠普、上海浦东软件园等共同创建，聚焦人工智能与半导体，服务世界500强企业。"
            "2027届校招：软件工程师(20名，西安，统招一本本科以上，要求C/C++/C#/Java至少一种、"
            "MySQL/Oracle等数据库至少一种，CET-4)；半导体芯片测试工程师(30名)；PCB硬件设计(10名)。"
            "福利：六险一金、弹性工作制、超长带薪年假、年终奖。"
        ),
    },
    "海信集团": {
        "fields": {
            "job_title": "软件算法类/硬件研发类/技术类2027校招（含软件开发工程师-嵌入式方向）",
            "location": "山东省·青岛市、广东省·佛山市为主（少量上海/四川）",
            "tech_stack": "软件开发工程师（嵌入式开发方向）等",
            "apply_method": "https://jobs.hisense.com（校园招聘板块）",
            "source": "浏览器补全-海信集团招聘官网",
        },
        "source": "https://jobs.hisense.com/jobs",
        "external": (
            "【浏览器补全·海信集团2027校招官网】海信集团，青岛大型家电/科技集团，技术立企，"
            "产业覆盖家电、半导体、智慧能源、激光、汽车电子等。2027届校招面向2027届本科及以上，"
            "岗位分软件算法类/硬件研发类/技术类等，共1261个在招职位，含软件开发工程师（嵌入式开发方向）等；"
            "工作地以青岛、佛山为主，另有上海/四川/北京等地。"
        ),
    },
    "字节跳动": {
        "fields": {
            "job_title": "2027届校招（前端、后端、客户端、算法等全岗位）",
            "location": "北京/上海/深圳/成都/杭州等多地",
            "tech_stack": "后端开发岗（技术栈不限，以实习/校招投递为准）",
            "apply_method": "https://jobs.bytedance.com/campus",
            "source": "浏览器补全-字节跳动校园招聘官网",
        },
        "source": "https://jobs.bytedance.com/campus",
        "external": (
            "【浏览器补全·字节跳动2027校招官网】字节跳动，全球互联网科技公司，旗下产品抖音/TikTok/今日头条/飞书等，"
            "业务覆盖150个国家和地区。2027届校园招聘面向2026年9月至2027年8月毕业的同学，"
            "前端、后端、客户端、算法等岗位全面开放，工作地覆盖北京/上海/深圳/成都/杭州等。"
            "非外企，大厂薪资高但工作节奏快。"
        ),
    },
    "龙旗": {
        "fields": {
            "job_title": "软件类、IT类、硬件类等2027校招",
            "location": "上海/深圳/西安等研发中心（总部上海）",
            "tech_stack": "软件类岗位（ODM智能终端，具体技术栈以岗位发布为准）",
            "apply_method": "https://longcheerzp1.zhiye.com",
            "source": "浏览器补全-西电就业网校招公告",
        },
        "source": "https://job.xidian.edu.cn/campus/view/id/756946",
        "external": (
            "【浏览器补全·龙旗科技2027校招（西电就业网公告）】上海龙旗科技股份有限公司，智能终端ODM龙头，"
            "服务全球头部消费电子品牌，产品覆盖智能手机、AI PC、汽车电子、智能眼镜、机器人等，"
            "A股上市(603341.SH)，《财富》中国500强。总部上海，七大研发中心设于上海、深圳、惠州、南昌、合肥、"
            "西安及苏州。2027届校招热招岗位：软件类、IT类、硬件类、测试类等。"
            "福利：竞争力薪酬、五险一金+补充商业保险、车贴餐贴。网申：longcheerzp1.zhiye.com。"
        ),
    },
    "神龙汽车": {
        "fields": {
            "job_title": "数字化岗、技术及制造岗、营销岗等2027校招",
            "location": "湖北省·武汉市",
            "education": "大学本科及以上",
            "tech_stack": "数字化岗：人工智能/软件工程/计算机/大数据等专业（具体岗位详见发布页）",
            "apply_method": "https://app.mokahr.com/campus-recruitment/dfmc/170464",
            "source": "浏览器补全-西电就业网校招简章",
        },
        "source": "https://job.xidian.edu.cn/campus/view/id/757647",
        "external": (
            "【浏览器补全·神龙汽车2027校招简章（西电就业网）】神龙汽车有限公司，东风汽车集团与Stellantis集团"
            "各持股50%合资（法系外企合资背景），旗下东风雪铁龙、东风标致、东风富康品牌，2026年8月新设"
            "神龙汽车科技(武汉)（注册82亿元）向电动化智能化转型。2027届校招岗位：数字化岗（人工智能/软件工程/"
            "数据科学与大数据技术/计算机等专业）、营销岗、技术及制造岗、职能管理岗；"
            "工作地点湖北武汉；要求本科及以上，数字化岗需CET-6。福利：五险一金+商业保险、住房补贴、带薪年假。"
        ),
    },
    "深圳石犀科技": {
        "fields": {
            "job_title": "校招岗位（具体岗位信息有限）",
            "location": "广东省·深圳市",
            "tech_stack": "未公开详细技术栈要求",
            "apply_method": "",
            "source": "浏览器补全-公开检索（公司信息有限）",
        },
        "source": "https://www.jobui.com/company/24431810/",
        "external": (
            "【浏览器补全·深圳石犀科技】深圳市石犀科技有限公司，注册地深圳，公开信息有限（规模较小），"
            "BOSS直聘/牛客有2026校招职位信息，具体岗位与薪资未公开披露，无法核实技术栈与WLB。"
        ),
    },
    "王力安防科技": {
        "fields": {
            "job_title": "2027届校招（研发、制造、市场/营销、职能四大方向）",
            "location": "浙江省（总部永康）",
            "tech_stack": "研发方向含智能锁/安防产品相关",
            "apply_method": "官网 wanglianfang.com 招聘通道",
            "source": "浏览器补全-公开校招公告",
        },
        "source": "https://career.hebut.edu.cn/correcruit/content/id/",
        "external": (
            "【浏览器补全·王力安防2027校招公告】王力安防科技股份有限公司，1996年开创，2021年上交所A股上市"
            "（605268），全国安防门锁首家上市企业，主营安全门、木门及机械防盗锁、智能锁，总部浙江永康。"
            "2027届校园招聘面向2026/2027届本科及以上，专业不限，设研发、制造、市场/营销、职能四大方向。"
        ),
    },
    "中国电子科技集团公司第四十六研究所": {
        "fields": {
            "job_title": "2027届校招（半导体材料/光纤研究为主，软件类岗位少）",
            "location": "天津市",
            "tech_stack": "以半导体材料、光纤、工艺研究为主",
            "apply_method": "https://46.cetc.com.cn（人才招聘频道）",
            "source": "浏览器补全-中电科46所官网",
        },
        "source": "https://46.cetc.com.cn/rlzy/rczp/index.html",
        "external": (
            "【浏览器补全·中国电科46所】中国电子科技集团公司第四十六研究所，中国电科（军工央企）下属，"
            "国内最早从事半导体材料和光纤研究与生产的单位之一，位于天津。中国电科2027届校园招聘已启动，"
            "46所岗位以半导体材料、光纤器件、工艺研究为主，软件类岗位较少，工作地天津。"
        ),
    },
    "中国石油集团东方地球物理勘探有限责任公司": {
        "fields": {
            "job_title": "2027校招（地震数据成像、地质综合研究、计算机等方向）",
            "location": "河北省·涿州市",
            "tech_stack": "计算机方向（物探数据处理，偏C++/算法）",
            "apply_method": "https://zhaopin.cnpc.com.cn",
            "source": "浏览器补全-中石油招聘公告",
        },
        "source": "https://www.gaoxiaojob.com/announcement/detail/",
        "external": (
            "【浏览器补全·东方物探】中国石油集团东方地球物理勘探有限责任公司(BGP)，中国石油天然气集团"
            "全资物探专业化子公司（国企），以地球物理方法勘探油气资源。2027届校招含地震数据成像、"
            "地质综合研究、油气藏评价开发、计算机等方向，工作地以河北涿州为主。"
        ),
    },
    "网络空间部队某部": {
        "fields": {
            "job_title": "2026-2027年度直接选拔招录军官（网络空间方向）",
            "location": "面向全军分配（未公开具体驻地）",
            "tech_stack": "网络空间/计算机相关专业方向（军队直招军官）",
            "apply_method": "军队人才网 81rc.81.cn 公告",
            "source": "浏览器补全-军队人才网公告",
        },
        "source": "http://81rc.81.cn",
        "external": (
            "【浏览器补全·网络空间部队】中国人民解放军网络空间部队2026-2027年度直接选拔招录军官公告"
            "（军队人才网发布），面向双一流院校毕业生直接选拔招录军官，从事网络空间作战相关岗位，"
            "需服现役，工作地按军队部署分配。"
        ),
    },
}


def main() -> None:
    cfg = load_config(ROOT / "job_assistant" / "config" / "config.yaml")
    llm = HttpLLMClient(cfg.llm)
    scorer = JobScorer(llm, cfg.profile, threshold=cfg.recommend_score_threshold)
    jobs_dir = Path(cfg.data.paths["jobs"])

    filters = [a for a in sys.argv[1:]]
    done = 0
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        company = record.get("company_name", "")
        info = ENRICH.get(company)
        if info is None:
            continue
        if filters and not any(f in company for f in filters):
            continue

        job = JobPosting.from_dict(record)
        for key, value in info["fields"].items():
            setattr(job, key, value)
        job.incomplete_reason = ""
        # 补全场景：字段已从浏览器核实，历史复核标记（如"宣讲会地点误填"）一并解除
        job.review_reason = ""

        match = scorer.score(job, external_info=info["external"])
        if match.has_error:
            print(f"[失败] {company}: {match.error}")
            continue

        updated = job.to_dict()
        record.update(updated)
        record["score"] = match.score
        record["recommended"] = bool(match.is_recommended)
        record["threshold"] = match.threshold
        record["reason"] = match.reason
        record["incomplete_reason"] = ""
        record["review_reason"] = ""
        extra = record.setdefault("extra", {})
        extra["browser_enriched"] = True
        extra["enrich_source"] = info["source"]
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        done += 1
        print(f"[完成] {company} | {job.job_title[:40]} | score={match.score} | "
              f"推荐={match.is_recommended}")
    print(f"共处理 {done} 条")


if __name__ == "__main__":
    main()
